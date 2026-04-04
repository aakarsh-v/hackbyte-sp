# Optional AWS EC2 + Docker (Terraform)

Minimal Terraform to provision a single Ubuntu 22.04 instance, install Docker via the official convenience script, and optionally clone this repository. Matches the Executive Summary idea of “simple VM + Docker Compose” without storing secrets in Terraform.

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.0
- AWS account, `aws configure` credentials
- An EC2 Key Pair in the target region (`key_name`)

## Configure

1. Copy variables: create `terraform.tfvars` (gitignored) with at least:

   ```hcl
   key_name   = "your-keypair-name"
   ssh_cidr   = "203.0.113.10/32"  # your IP only
   repo_url   = "https://github.com/your-org/your-fork.git"
   ```

2. **Never** commit `terraform.tfvars` or `.env` containing `GEMINI_API_KEY`, `SPACETIME_BEARER_TOKEN`, etc.

## Apply

```bash
cd infra/terraform/ec2-docker
terraform init
terraform plan
terraform apply
```

Use `output public_ip` to SSH as `ubuntu` and then:

- Clone the repo if `repo_url` was empty
- Copy `.env.example` to `.env`, set secrets on the instance only
- From repo root: `npm run build:web` then `docker compose --profile local-spacetime -f infra/docker-compose.yml --env-file .env up -d --build`

## Security

- Restrict `ssh_cidr` to your IP.
- Tighten security group rules for ports 8000, 3000, 9090, etc. to your IP for demos.
- Do not expose the Docker socket to the public internet; this stack expects a trusted host.
