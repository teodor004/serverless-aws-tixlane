"""Process-wide test environment, applied before any tixlane module is imported.

AWS credentials are forced to dummies so no test can ever pick up a real profile.
"""

import os

for _name in ("AWS_PROFILE", "AWS_ENDPOINT_URL", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
    os.environ.pop(_name, None)

os.environ.update(
    {
        "AWS_DEFAULT_REGION": "eu-central-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "POWERTOOLS_SERVICE_NAME": "tixlane",
        "POWERTOOLS_METRICS_NAMESPACE": "TixLane",
        "POWERTOOLS_TRACE_DISABLED": "true",
        "POWERTOOLS_LOG_LEVEL": "WARNING",
    }
)
