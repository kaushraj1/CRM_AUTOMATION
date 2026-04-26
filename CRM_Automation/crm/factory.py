"""Factory — pick the right CRM backend by source name."""

from crm.base import CRMClient


def get_client(source: str) -> CRMClient:
    """Return a concrete CRMClient for the given source.

    Args:
        source: one of "airtable", "hubspot", "zoho"

    Raises:
        ValueError: if the source is unknown
    """
    source = (source or "").strip().lower()

    if source == "airtable":
        from crm.airtable_client import AirtableClient
        return AirtableClient()
    if source == "hubspot":
        from crm.hubspot_client import HubSpotClient
        return HubSpotClient()
    if source == "zoho":
        from crm.zoho_client import ZohoClient
        return ZohoClient()
    if source == "mock":
        from crm.mock_client import MockCRMClient
        return MockCRMClient()

    raise ValueError(
        f"Unknown CRM source: {source!r}. Supported: airtable, hubspot, zoho, mock."
    )
