variable "project_id" {
  type    = string
  default = "hackyeah-2026-510606"
}
variable "region" {
  type    = string
  default = "europe-west1"
}
variable "name" {
  type    = string
  default = "judge-api-relay"
}
variable "deploy_function" {
  type    = bool
  default = false
}
variable "enabled" {
  type    = bool
  default = false
}
variable "expires_at" {
  type    = string
  default = "2026-10-05T21:59:00Z"
  validation {
    condition     = can(formatdate("YYYY", var.expires_at))
    error_message = "expires_at must be a timezone-aware RFC3339 timestamp."
  }
}
variable "total_calls" {
  type    = number
  default = 1500
  validation {
    condition     = var.total_calls > 0 && floor(var.total_calls) == var.total_calls
    error_message = "total_calls must be a positive integer."
  }
}
variable "calls_per_minute" {
  type    = number
  default = 200
}
variable "max_body_bytes" {
  type    = number
  default = 32768
}
variable "max_output_tokens" {
  type    = number
  default = 2048
}
variable "openai_model" {
  type    = string
  default = "gpt-4o-mini"
}
variable "typesafe_model" {
  type    = string
  default = "jev-1.13.0"
}
variable "openai_secret_version" {
  type    = string
  default = "1"
  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.openai_secret_version))
    error_message = "Pin a numeric OpenAI secret version."
  }
}
variable "typesafe_secret_version" {
  type    = string
  default = "1"
  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.typesafe_secret_version))
    error_message = "Pin a numeric TypeSafe secret version."
  }
}
