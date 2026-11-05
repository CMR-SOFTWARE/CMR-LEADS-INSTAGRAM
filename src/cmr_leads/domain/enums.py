from enum import StrEnum


class LeadStatus(StrEnum):
    NEW = "new"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONTACTED = "contacted"
    REPLIED = "replied"
    MEETING = "meeting"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"


class SourceType(StrEnum):
    CMR_FOLLOWING = "cmr_following"
    CMR_FOLLOWERS = "cmr_followers"
    DISCOVERED_FROM_FOLLOWING = "discovered_from_following"
    DISCOVERED_FROM_FOLLOWERS = "discovered_from_followers"
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"
    GOOGLE_PLACES = "google_places"
    OSM = "osm"
    INSTAGRAM_DM = "instagram_dm"


class IdentifierType(StrEnum):
    IG_ID = "ig_id"
    IG_USERNAME = "ig_username"
    PHONE = "phone"
    DOMAIN = "domain"
    EMAIL = "email"
    PLACE_ID = "place_id"
    NAME_ADDRESS = "name_address"


class EnrichmentState(StrEnum):
    PENDING = "pending"
    DONE = "done"
    NOT_FOUND = "not_found"
    ERROR = "error"


class Priority(StrEnum):
    LOW = "baja"
    POSSIBLE = "posible"
    HIGH = "interesante"


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    INSTAGRAM_DM = "instagram_dm"
    EMAIL = "email"
    PHONE = "phone"
    IN_PERSON = "in_person"
    OTHER = "other"


class LostReason(StrEnum):
    PRICE = "precio"
    TIMING = "timing"
    NOT_INTERESTED = "no_interesa"
    CHOSE_OTHER = "eligio_otro"
    NO_RESPONSE = "sin_respuesta"


class WebStatus(StrEnum):
    NONE = "no"
    OK = "si"
    OLD = "vieja"


class ProposalStatus(StrEnum):
    DRAFT = "borrador"
    SENT = "enviada"
    ACCEPTED = "aceptada"
    REJECTED = "rechazada"
