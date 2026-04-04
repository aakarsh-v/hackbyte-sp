terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

resource "aws_security_group" "devops_ai_demo" {
  name        = "devops-ai-demo-sg"
  description = "Hackathon demo: SSH, app, Grafana, Prometheus, SpacetimeDB host port"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  ingress {
    description = "FastAPI / console"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Grafana"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Prometheus"
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SpacetimeDB HTTP (host mapped)"
    from_port   = 3004
    to_port     = 3004
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Mini frontend"
    from_port   = 3001
    to_port     = 3001
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Auth / payment services"
    from_port   = 8081
    to_port     = 8082
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  user_data = <<-EOF
    #!/bin/bash
    set -euxo pipefail
    apt-get update
    apt-get install -y ca-certificates curl git
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker ubuntu
    ${var.repo_url != "" ? "sudo -u ubuntu git clone ${var.repo_url} /home/ubuntu/hackbyte  || true" : "# Set repo_url to clone automatically"}
    echo "Docker installed. Clone the repo, configure .env, then: cd hackbyte && npm run build:web && docker compose --profile local-spacetime -f infra/docker-compose.yml --env-file .env up -d --build" >> /var/log/user-data.log
  EOF
}

resource "aws_instance" "devops_ai" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.devops_ai_demo.id]
  key_name               = var.key_name
  user_data              = local.user_data

  tags = {
    Name = "devops-ai-demo"
  }
}

output "public_ip" {
  value       = aws_instance.devops_ai.public_ip
  description = "SSH: ssh -i <key.pem> ubuntu@<public_ip>"
}
