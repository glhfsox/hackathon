locals {
  apis = toset([
    "cloudfunctions.googleapis.com", "run.googleapis.com", "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com", "storage.googleapis.com", "secretmanager.googleapis.com",
    "firestore.googleapis.com", "iam.googleapis.com"
  ])
  secrets = toset(["openai", "typesafe"])
}

resource "google_project_service" "api" {
  for_each           = local.apis
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "runtime" {
  account_id   = "${var.name}-runtime"
  display_name = "Judge relay runtime"
  depends_on   = [google_project_service.api]
}

resource "google_service_account" "build" {
  account_id   = "${var.name}-build"
  display_name = "Judge relay build"
  depends_on   = [google_project_service.api]
}

resource "google_project_iam_member" "builder" {
  for_each = toset(["roles/logging.logWriter", "roles/artifactregistry.writer", "roles/storage.objectViewer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.build.email}"
}

resource "google_project_iam_member" "datastore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
  # Restrict database access to this relay's named database.
  condition {
    title      = "judge-relay-database"
    expression = "resource.name == 'projects/${var.project_id}/databases/${var.name}'"
  }
}

resource "google_secret_manager_secret" "provider" {
  for_each  = local.secrets
  secret_id = "${var.name}-${each.key}"
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
  depends_on = [google_project_service.api]
}

resource "google_secret_manager_secret_iam_member" "reader" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.provider[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_firestore_database" "quota" {
  project                 = var.project_id
  name                    = var.name
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "ABANDON"
  depends_on              = [google_project_service.api]
}

resource "google_storage_bucket" "source" {
  name                        = "${var.project_id}-${var.name}-source"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  depends_on                  = [google_project_service.api]
}

data "archive_file" "source" {
  type        = "zip"
  source_dir  = "${path.module}/function"
  output_path = "${path.module}/source.zip"
  excludes    = ["__pycache__", ".pytest_cache", ".ruff_cache"]
}

resource "google_storage_bucket_object" "source" {
  name   = "relay-${data.archive_file.source.output_md5}.zip"
  bucket = google_storage_bucket.source.name
  source = data.archive_file.source.output_path
}

resource "google_cloudfunctions2_function" "relay" {
  count       = var.deploy_function ? 1 : 0
  name        = var.name
  location    = var.region
  description = "Temporary public provider transport for local judge demos"
  build_config {
    runtime         = "python312"
    entry_point     = "relay"
    service_account = google_service_account.build.id
    source {
      storage_source {
        bucket = google_storage_bucket.source.name
        object = google_storage_bucket_object.source.name
      }
    }
  }
  service_config {
    service_account_email            = google_service_account.runtime.email
    available_memory                 = "256Mi"
    available_cpu                    = "1"
    timeout_seconds                  = 60
    min_instance_count               = 0
    max_instance_count               = 2
    max_instance_request_concurrency = 8
    ingress_settings                 = "ALLOW_ALL"
    environment_variables = {
      RELAY_SETTINGS = jsonencode({
        enabled            = var.enabled
        expires_at         = var.expires_at
        total_calls        = var.total_calls
        calls_per_minute   = var.calls_per_minute
        max_body_bytes     = var.max_body_bytes
        max_output_tokens  = var.max_output_tokens
        openai_model       = var.openai_model
        typesafe_model     = var.typesafe_model
        upstream_timeout_s = 45
        project_id         = var.project_id
        database           = google_firestore_database.quota.name
      })
    }
    secret_environment_variables {
      key        = "OPENAI_API_KEY"
      project_id = var.project_id
      secret     = google_secret_manager_secret.provider["openai"].secret_id
      version    = var.openai_secret_version
    }
    secret_environment_variables {
      key        = "TYPESAFE_API_KEY"
      project_id = var.project_id
      secret     = google_secret_manager_secret.provider["typesafe"].secret_id
      version    = var.typesafe_secret_version
    }
  }
  depends_on = [
    google_project_iam_member.builder, google_project_iam_member.datastore,
    google_secret_manager_secret_iam_member.reader
  ]
}

resource "google_cloud_run_service_iam_member" "public" {
  count    = var.deploy_function ? 1 : 0
  location = var.region
  service  = google_cloudfunctions2_function.relay[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "relay_url" {
  value = try(google_cloudfunctions2_function.relay[0].service_config[0].uri, null)
}
