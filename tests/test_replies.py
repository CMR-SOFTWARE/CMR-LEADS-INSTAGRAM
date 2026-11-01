import json
from types import SimpleNamespace

import pytest

from cmr_leads.domain.enums import Channel, LeadStatus, SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.pipeline.ingest import ingest
from cmr_leads.pipeline.learning import lead_label
from cmr_leads.replies.ai_classifier import ClaudeReplyClassifier
from cmr_leads.replies.classifier import ReplyClassifier, classify_with_rules
from cmr_leads.replies.service import import_whatsapp_chat, register_reply, reply_stats
from cmr_leads.replies.whatsapp_export import contact_from_filename, parse_chat
from cmr_leads.services import mark_contacted


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Hola! Sí, me interesa, cuándo podemos hablar?", "interesado"),
        ("Cuánto sale una página?", "pide_info"),
        ("Por ahora no, más adelante capaz", "ahora_no"),
        ("No gracias, no nos interesa", "no_interesa"),
        ("No me escriban más por favor", "no_contactar"),
        ("¡Gracias por comunicarte con Pelu Lu! En breve te respondemos.", "automatica"),
        ("👍", "otro"),
    ],
)
def test_rules_categories(text, category):
    assert classify_with_rules(text).category == category


def test_rules_objections():
    analysis = classify_with_rules("Gracias, pero ya tenemos página y está muy caro todo")
    assert set(analysis.objections) == {"ya_tiene", "precio"}


ANDROID = """18/10/26, 10:01 - Los mensajes y las llamadas están cifrados de extremo a extremo.
18/10/26, 10:02 - Tomás: Hola, ¿cómo están? Soy Tomás de CMR.
Desarrollamos sistemas de turnos.
18/10/26, 15:40 - Pelu Lu: Hola! Sí, me interesa
18/10/26, 15:41 - Pelu Lu: cuánto sale?
19/10/26, 9:00 - Tomás: Te paso info
"""

IPHONE = """[18/10/26, 10:02:11 a. m.] Tomás: Hola! Soy Tomás de CMR
[18/10/26, 3:40:00 p. m.] Gym Fuerza: Por ahora no, gracias
"""


def test_parse_android_multiline_and_system_lines():
    messages = parse_chat(ANDROID)
    assert [m.sender for m in messages] == ["Tomás", "Pelu Lu", "Pelu Lu", "Tomás"]
    assert "turnos" in messages[0].text
    assert messages[1].at.hour == 15


def test_parse_iphone_ampm():
    messages = parse_chat(IPHONE)
    assert messages[1].sender == "Gym Fuerza"
    assert messages[1].at.hour == 15 and messages[0].at.hour == 10


def test_contact_from_filename():
    assert contact_from_filename("Chat de WhatsApp con Pelu Lu.txt") == "Pelu Lu"


def _lead(session, workspace, name="Pelu Lu"):
    return ingest(
        session,
        workspace,
        SourceRecord(profile=RawProfile(display_name=name), source=SourceType.MANUAL),
        "AR",
    ).lead


def test_import_chat_groups_replies_and_updates_status(session, workspace):
    lead = _lead(session, workspace)
    result = import_whatsapp_chat(
        session, lead, ANDROID, ReplyClassifier(), "Chat de WhatsApp con Pelu Lu.txt", my_name=None
    )
    assert result.error is None and result.my_name == "Tomás"
    assert result.replies == 1 and result.outbound == 1
    inbound = [i for i in lead.interactions if i.direction == "inbound"]
    assert inbound[0].message == "Hola! Sí, me interesa\ncuánto sale?"
    assert inbound[0].outcome == "interesado"
    assert lead.status == LeadStatus.REPLIED
    # Volver a subir el mismo chat no duplica.
    again = import_whatsapp_chat(
        session, lead, ANDROID, ReplyClassifier(), "x.txt", my_name="Tomás"
    )
    assert again.replies == 0


def test_import_chat_needs_my_name_when_ambiguous(session, workspace):
    lead = _lead(session, workspace)
    result = import_whatsapp_chat(
        session, lead, IPHONE, ReplyClassifier(), "chat.txt", my_name=None
    )
    assert "nombre" in result.error


def test_no_contactar_reply_marks_dnc(session, workspace):
    lead = _lead(session, workspace)
    mark_contacted(session, lead, Channel.WHATSAPP, "hola", "pagina_web")
    register_reply(session, lead, "No me escriban más", ReplyClassifier())
    assert lead.do_not_contact and lead.status == LeadStatus.LOST
    assert lead_label(lead) == -1


def test_no_interesa_marks_lost_and_learning_label(session, workspace):
    lead = _lead(session, workspace)
    mark_contacted(session, lead, Channel.WHATSAPP, "hola")
    register_reply(session, lead, "No gracias, no nos interesa", ReplyClassifier())
    assert lead.status == LeadStatus.LOST and lead.next_follow_up_at is None


def test_reply_stats_by_template(session, workspace):
    a, b, c = (_lead(session, workspace, n) for n in ("A", "B", "C"))
    for lead, template in ((a, "sistema_turnos"), (b, "sistema_turnos"), (c, "pagina_web")):
        mark_contacted(session, lead, Channel.WHATSAPP, "hola", template)
    register_reply(session, a, "Me interesa!", ReplyClassifier())
    register_reply(
        session, b, "Gracias por comunicarte, en breve te respondemos", ReplyClassifier()
    )
    stats = reply_stats(session, workspace.id)
    assert stats.contacted == 3 and stats.replied == 1 and stats.positive == 1
    by_template = dict(stats.by["Mensaje"])
    assert by_template["sistema_turnos"].contacted == 2
    assert by_template["sistema_turnos"].positive_rate == 0.5


class FakeAnthropic:
    def __init__(self, payload, stop_reason="end_turn"):
        self.calls = []
        payload_text = json.dumps(payload)

        def create(**kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=payload_text)]
            )

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def test_claude_classifier_uses_structured_output():
    fake = FakeAnthropic(
        {
            "category": "pide_info",
            "objections": ["precio", "inventada"],
            "summary": "Pregunta el precio",
            "suggested_action": "Mandar precios",
        }
    )
    classifier = ClaudeReplyClassifier("k", client=fake)
    analysis = classifier.classify("cuánto sale?", {"negocio": "Pelu"})
    assert analysis.category == "pide_info" and analysis.objections == ["precio"]
    assert analysis.source == "ia"
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"


def test_ai_refusal_or_error_falls_back_to_rules():
    refusing = ClaudeReplyClassifier("k", client=FakeAnthropic({}, stop_reason="refusal"))
    assert ReplyClassifier(refusing).classify("Me interesa!").source == "reglas"

    class Broken:
        def classify(self, text, context):
            raise RuntimeError("sin internet")

    assert ReplyClassifier(Broken()).classify("No me interesa").category == "no_interesa"


def test_stats_count_leads_that_replied_without_registered_contact(session, workspace):
    lead = _lead(session, workspace)
    register_reply(session, lead, "Me interesa", ReplyClassifier())
    stats = reply_stats(session, workspace.id)
    assert stats.contacted == 1 and stats.positive == 1
