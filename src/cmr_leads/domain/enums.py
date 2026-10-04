from enum import StrEnum


class LeadStatus(StrEnum):
    NEW = "new"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONTACTED = "contacted"
    REPLIED = "replied"
    MEETING = "meeting"
    WON = "won"
    LOST = "lost"


class SourceType(StrEnum):
    CMR_FOLLOWING = "cmr_following"
    CMR_FOLLOWERS = "cmr_followers"
    DISCOVERED_FROM_FOLLOWING = "discovered_from_following"
    DISCOVERED_FROM_FOLLOWERS = "discovered_from_followers"
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"


class IdentifierType(StrEnum):
    IG_ID = "ig_id"
    IG_USERNAME = "ig_username"
    PHONE = "phone"
    DOMAIN = "domain"
    EMAIL = "email"


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
