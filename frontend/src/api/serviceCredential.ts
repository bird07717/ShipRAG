export const SERVICE_CREDENTIAL_REJECTED_EVENT = "rag-service-auth-rejected";

/**
 * Why the credential was rejected. "missing" means the request layer never
 * sent anything (no token was configured yet) — an onboarding state, not a
 * failure. "invalid" means the server actually returned 401 for a token
 * that was present, i.e. the token is stale, revoked, or wrong.
 */
export type ServiceCredentialRejectionReason = "missing" | "invalid";

const SERVICE_TOKEN_STORAGE_KEY = "rag_service_token";

/** Shared HTTP error carrying enough detail to distinguish a local guard
 * rejection (never reached the network) from a real backend response. */
export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export function readServiceCredential(): string {
  try {
    return sessionStorage.getItem(SERVICE_TOKEN_STORAGE_KEY)?.trim() ?? "";
  } catch {
    return "";
  }
}

export function saveServiceCredential(value: string): void {
  const token = value.trim();
  try {
    if (token) sessionStorage.setItem(SERVICE_TOKEN_STORAGE_KEY, token);
    else sessionStorage.removeItem(SERVICE_TOKEN_STORAGE_KEY);
  } catch {
    // The request layer will reject locally when storage is unavailable.
  }
}

export function clearServiceCredential(): void {
  try {
    sessionStorage.removeItem(SERVICE_TOKEN_STORAGE_KEY);
  } catch {
    // Keep rejection propagation working in privacy-restricted browsers.
  }
}

export function announceServiceCredentialRejected(
  reason: ServiceCredentialRejectionReason,
): void {
  clearServiceCredential();
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent<{ reason: ServiceCredentialRejectionReason }>(
        SERVICE_CREDENTIAL_REJECTED_EVENT,
        { detail: { reason } },
      ),
    );
  }
}
