# Research

- Decision: HTTP Cloud Run function managed through Terraform, so only provider transport is hosted. [Official tutorial](https://docs.cloud.google.com/functions/docs/tutorials/terraform).
- Decision: create secret containers/IAM with Terraform, upload values separately through stdin, pin numeric version IDs. Sensitive Terraform values still enter state. [Terraform guidance](https://developer.hashicorp.com/terraform/language/manage-sensitive-data), [secret upload](https://docs.cloud.google.com/secret-manager/docs/add-secret-version).
- Decision: atomic Firestore reservation before HTTP; retries never execute provider calls. [Transactions](https://firebase.google.com/docs/firestore/manage-data/transactions).
- Rejected: instance limits or memory as a global quota; restarts and temporary overscaling break guarantees. [Instance behavior](https://docs.cloud.google.com/run/docs/configuring/max-instances).
- Limitation: request/byte/token quotas bound usage, not exact provider dollars or all cloud charges. [Billing budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets) are not an external-provider cap.
