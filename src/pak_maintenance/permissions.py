"""Central role boundaries for operational work and confidential personnel data."""

CHIEFS = ("head",)
PM_PROPOSERS = ("planner", "supervisor", "manager")
OPERATIONAL_EDITORS = ("head", "manager", "planner")
PERSONNEL_ADMINISTRATORS = ("head", "admin")


def can_view_personnel_reports(user):
    return bool(user and user.active and user.role == "head")


def may_view_pm_archive(user):
    """The dedicated audit archive belongs solely to the PM/NET planner."""
    return bool(user and user.active and user.role == "planner")


def can_view_grades(user, specialty=None):
    """A supervisor may inspect only the evidence for their own unit's grading."""
    return bool(user and user.active and (user.role == "head" or
                user.role == "supervisor" and specialty is not None and user.specialty == specialty))
