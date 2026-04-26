from datetime import UTC, datetime

from aq_api.models import HealthStatus


def current_health_status() -> HealthStatus:
    return HealthStatus(status="ok", timestamp=datetime.now(UTC))
