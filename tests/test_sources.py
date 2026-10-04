import zipfile

from cmr_leads.domain.enums import SourceType
from cmr_leads.sources.csv_import import CsvSource
from cmr_leads.sources.instagram_export import InstagramExportSource

from .conftest import FIXTURES


def test_instagram_export_from_folder():
    records = list(InstagramExportSource(FIXTURES, "cmr.software").discover())
    by_source = {}
    for record in records:
        by_source.setdefault(record.source, []).append(record.profile.instagram_username)
    assert by_source[SourceType.CMR_FOLLOWERS] == ["estetica.sofi", "pizzeria_don_pepe"]
    assert by_source[SourceType.CMR_FOLLOWING] == ["pizzeria_don_pepe", "gym.fuerza"]
    assert all(r.source_ref == "cmr.software" for r in records)


def test_instagram_export_from_zip(tmp_path):
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(
            FIXTURES / "followers_1.json", "connections/followers_and_following/followers_1.json"
        )
        zf.writestr("connections/other/ignored.json", "[]")
    records = list(InstagramExportSource(archive).discover())
    assert [r.profile.instagram_username for r in records] == ["estetica.sofi", "pizzeria_don_pepe"]


def test_csv_source_with_aliases_and_semicolons(tmp_path):
    path = tmp_path / "relevamiento.csv"
    path.write_text(
        "Instagram;Nombre;Telefono;Ciudad\n@taller.ruiz;Taller Ruiz;0336 442-1234;San Nicolás\n;;;\n",
        encoding="utf-8",
    )
    records = list(CsvSource(path).discover())
    assert len(records) == 1
    profile = records[0].profile
    assert profile.instagram_username == "@taller.ruiz"
    assert profile.display_name == "Taller Ruiz"
    assert profile.city == "San Nicolás"
    assert records[0].source == SourceType.CSV_IMPORT
