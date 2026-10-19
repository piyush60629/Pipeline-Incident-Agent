# Runbook: Authentication and Access Failures

## Symptoms
- ADF copy activities fail with `Unauthorized`, `InvalidAuthenticationInfo`, `Server failed to authenticate the request`, or `AADSTS7000222`.
- `Forbidden` or `AuthorizationPermissionMismatch` means the identity authenticated but lacks permission on the path.

## Expired service principal secret
1. Run "Test connection" on the linked service and check the secret expiry date.
2. Create a new client secret for the app registration (validity 12 months) in Microsoft Entra ID.
3. Update the secret value in Azure Key Vault (`kv-retail-prod`); linked services read it at runtime, so no ADF redeployment is needed.
4. Test the linked service connection, then rerun the failed pipeline after approval.
5. Prevention: add a Key Vault expiry alert 30 days before expiry and prefer managed identities where the source supports them.

## Missing role or ACL (403 AuthorizationPermissionMismatch)
1. Identify the identity used by the linked service (managed identity `adf-retail-prod` or a service principal).
2. Check recent IAM changes on the storage account or container (Activity Log, governance tickets).
3. The identity needs `Storage Blob Data Contributor` on the container for writes, `Storage Blob Data Reader` for reads.
4. Role assignments are owned by the cloud governance team: raise a ticket referencing the change that removed access. Data engineers cannot grant roles in production.
5. Role assignments take up to 10 minutes to propagate; rerun after the role is restored.
