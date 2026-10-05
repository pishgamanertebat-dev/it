"""Organizational authorization, independent of registration and profiles."""
from .store import (AuthorizationStore, DAILY_RECEIVE, OVERFLOW_READ, OFFICE_SUPERVISOR,
                    BUSINESS_ADMIN, FUNCTION_READ, DRIVER_READ, DRIVER_RECEIVE,
                    MECHANICAL_STAFF, MECHANICAL_MANAGER, MECHANICAL_MANAGER_DEPUTY,
                    MAINTENANCE_RECORDS_READ, DRIVER_REPORT_READ, MECH_OVERFLOW_RECEIVE,
                    MECH_DRIVER_RECEIVE, FILE_RESOURCES, FUNCTION_CAPABILITIES,
                    FunctionScope, RecipientSetResolution)
