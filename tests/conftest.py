"""Default test collection excludes the AWS integration suite.

The `integration/` tests hit real resources in us-east-1 and require AWS
credentials. They are opt-in: set RUN_INTEGRATION=1 to collect them.
"""
import os

collect_ignore = []
if not os.getenv("RUN_INTEGRATION"):
    collect_ignore.append("integration")
