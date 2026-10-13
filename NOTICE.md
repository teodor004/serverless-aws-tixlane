# NOTICE

TixLane is an original, clean-room work. All code, infrastructure definitions, pipelines,
naming and documents were written from scratch for this repository. Nothing was copied from,
or derived from, the code, IaC, templates or internal documentation of any employer, client
or internship.

The design draws on publicly available documentation and widely published patterns, listed
below. These are cited as background reading; no text or code was reproduced from them.
Whether each source is current is tracked separately in `docs/VERIFICATION_LOG.md`.

## Public sources consulted

| Topic | Source |
|---|---|
| AWS service behaviour (API Gateway HTTP APIs, Lambda, DynamoDB, SQS, SNS, S3, KMS, Cognito, CloudWatch, X-Ray, IAM) | AWS documentation — https://docs.aws.amazon.com/ |
| AWS Well-Architected Framework (six pillars) | https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html |
| GitHub Actions OIDC with AWS | GitHub docs — https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services |
| configure-aws-credentials action | https://github.com/aws-actions/configure-aws-credentials |
| OpenTofu language, S3 backend, state encryption | https://opentofu.org/docs/ |
| AWS provider for OpenTofu/Terraform | https://registry.terraform.io/providers/hashicorp/aws/latest/docs |
| Powertools for AWS Lambda (Python) | https://docs.powertools.aws.dev/lambda/python/latest/ |
| Moto | https://docs.getmoto.org/ |
| uv | https://docs.astral.sh/uv/ |
| Ruff, mypy, pydantic | https://docs.astral.sh/ruff/, https://mypy.readthedocs.io/, https://docs.pydantic.dev/ |
| tflint, Trivy, Checkov, gitleaks, Infracost | https://github.com/terraform-linters/tflint, https://trivy.dev/, https://www.checkov.io/, https://github.com/gitleaks/gitleaks, https://www.infracost.io/docs/ |
| k6 | https://grafana.com/docs/k6/latest/ |
| Floci and MiniStack emulators | Official project repositories (URLs to be confirmed in P0; VERIFICATION_LOG V-001–V-004) |
| MADR (Markdown Architectural Decision Records) | https://adr.github.io/madr/ |
| C4 model | https://c4model.com/ |
| STRIDE threat modelling | Microsoft threat-modelling documentation (public) |
| Claude Code settings and hooks | https://docs.anthropic.com/en/docs/claude-code |

## Third-party software

Runtime dependencies (installed at build time, not vendored): `aws-lambda-powertools`
(MIT-0), `pydantic` (MIT), `aws-xray-sdk` (Apache-2.0, via Powertools' `tracer` extra). Their
licences apply to their code. Development tools are used unmodified under their own licences.
