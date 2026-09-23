export const SERVICE_CREDENTIAL_REJECTED_EVENT = "rag-service-auth-rejected";

const SERVICE_TOKEN_STORAGE_KEY = "rag_service_token";

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

export function announceServiceCredentialRejected(): void {
  clearServiceCredential();
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(SERVICE_CREDENTIAL_REJECTED_EVENT));
  }
}
