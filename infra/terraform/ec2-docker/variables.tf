variable "aws_region" {
  type        = string
  description = "AWS region for the demo VM"
  default     = "us-east-1"
}

variable "instance_type" {
  type        = string
  description = "EC2 instance size (free tier: t2.micro or t3.micro where available)"
  default     = "t3.micro"
}

variable "ssh_cidr" {
  type        = string
  description = "CIDR allowed to SSH (restrict to your IP, e.g. 203.0.113.10/32)"
  default     = "0.0.0.0/0"
}

variable "key_name" {
  type        = string
  description = "Name of an existing EC2 Key Pair for SSH"
}

variable "repo_url" {
  type        = string
  description = "Git clone URL for this project (HTTPS or SSH)"
  default     = ""
}
