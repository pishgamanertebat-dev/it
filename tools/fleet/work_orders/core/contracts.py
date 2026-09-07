from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WorkOrderTypeSpec:
    """
    Static definition of one Work Order type.

    The generic Work Order Core uses this metadata without
    knowing the internal document/template implementation.
    """

    code: str
    label_fa: str
    menu_order: int

    # True only when this type can currently issue real orders.
    operational: bool

    # Internal staff routing role.
    staff_role: Optional[str] = None

    # Work-order numbering prefix.
    number_prefix: Optional[str] = None

    # Template identity/version.
    template_key: Optional[str] = None

    # Future Python implementation responsible for
    # type-specific document generation.
    builder_module: Optional[str] = None


    def validate(self) -> None:

        if not self.code.strip():
            raise ValueError(
                "Work Order type code cannot be empty"
            )

        if not self.label_fa.strip():
            raise ValueError(
                f"{self.code}: Persian label cannot be empty"
            )

        if self.menu_order < 1:
            raise ValueError(
                f"{self.code}: menu_order must be >= 1"
            )

        if self.operational:

            if not self.staff_role:
                raise ValueError(
                    f"{self.code}: operational type "
                    "must define staff_role"
                )

            if not self.number_prefix:
                raise ValueError(
                    f"{self.code}: operational type "
                    "must define number_prefix"
                )

            if not self.template_key:
                raise ValueError(
                    f"{self.code}: operational type "
                    "must define template_key"
                )