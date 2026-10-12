export type SignInPage = "showcase" | "classic";

const STORAGE_KEY = "perseverer.signInPage";

/** The sign-in page must be selectable before account settings can be loaded. */
export function getSignInPage(): SignInPage {
  return localStorage.getItem(STORAGE_KEY) === "classic" ? "classic" : "showcase";
}

export function setSignInPage(page: SignInPage): void {
  localStorage.setItem(STORAGE_KEY, page);
}
