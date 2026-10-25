import re
from pathlib import Path

# Banned patterns that bypass the single injected ClockService
BANNED_PATTERNS = [
    (re.compile(r"\bdatetime\.now\b"), "datetime.now"),
    (re.compile(r"\bdatetime\.utcnow\b"), "datetime.utcnow"),
    (re.compile(r"\bdatetime\.today\b"), "datetime.today"),
    (re.compile(r"\bdate\.today\b"), "date.today"),
    (re.compile(r"\btime\.time\("), "time.time("),
    (re.compile(r"\bfunc\.now\b"), "func.now"),
    (re.compile(r"server_default=.*now", re.IGNORECASE), "server_default=...now"),
    (re.compile(r"\bCURRENT_DATE\b"), "CURRENT_DATE"),
    (re.compile(r"\bCURRENT_TIMESTAMP\b"), "CURRENT_TIMESTAMP"),
    (re.compile(r"\bNOW\(\)"), "NOW()"),
]

DIRECTORIES_TO_SCAN = ["app", "scripts", "alembic"]


def test_no_direct_hardware_time_access():
    """Enforces that all date and time logic routes through ClockService.
    
    Allowlist rule: Only lines inside clock_service.py marked with '# clock-allow'
    are permitted to access real hardware time. All other code in app/, scripts/,
    and alembic/ migrations must inject ClockService.
    """
    root_dir = Path(__file__).resolve().parent.parent
    violations = []

    for dir_name in DIRECTORIES_TO_SCAN:
        target_dir = root_dir / dir_name
        if not target_dir.exists():
            continue

        for file_path in target_dir.rglob("*.py"):
            # Skip compiled or virtual env files
            if "__pycache__" in file_path.parts:
                continue

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line_idx, line in enumerate(f, start=1):
                    # Check if line contains allowlist tag
                    has_allow_tag = "# clock-allow" in line

                    for regex, pattern_name in BANNED_PATTERNS:
                        if regex.search(line):
                            # The allow tag is ONLY valid inside clock_service.py
                            if has_allow_tag and file_path.name == "clock_service.py":
                                continue

                            rel_path = file_path.relative_to(root_dir)
                            violations.append(
                                f"{rel_path}:{line_idx} - Banned pattern '{pattern_name}' found: {line.strip()}"
                            )

    assert not violations, (
        f"Found {len(violations)} banned direct time calls outside ClockService allowlist:\n"
        + "\n".join(violations)
    )


def test_real_now_only_in_auth_security_modules():
    """ClockService.real_now() is strictly restricted to session/token expiration in security modules.
    
    Any invocation of real_now() in business logic, temporal scheduling, or audit logging
    is a critical architectural violation.
    """
    root_dir = Path(__file__).resolve().parent.parent
    allowed_files = {"clock_service.py", "security.py"}
    violations = []

    real_now_regex = re.compile(r"\breal_now\b")

    for dir_name in DIRECTORIES_TO_SCAN:
        target_dir = root_dir / dir_name
        if not target_dir.exists():
            continue

        for file_path in target_dir.rglob("*.py"):
            if "__pycache__" in file_path.parts:
                continue

            if file_path.name in allowed_files:
                continue

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line_idx, line in enumerate(f, start=1):
                    if real_now_regex.search(line):
                        rel_path = file_path.relative_to(root_dir)
                        violations.append(
                            f"{rel_path}:{line_idx} - Unauthorized real_now() call: {line.strip()}"
                        )

    assert not violations, (
        f"Found {len(violations)} unauthorized references to real_now() outside auth/security:\n"
        + "\n".join(violations)
    )
