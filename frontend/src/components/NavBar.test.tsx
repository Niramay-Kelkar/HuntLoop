import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NavBar } from "./NavBar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/jobs",
}));

// NavBar's admin-link visibility (useIsAdmin()) depends on Clerk's
// useAuth() + a real GET /admin/whoami call - mocked here the same way
// every other real external call in this suite is mocked, so these
// tests never need a real ClerkProvider/signed-in session. Default:
// signed out, so most tests exercise the "most visitors" path with no
// extra setup.
type MockAuthState = { isSignedIn: boolean; userId: string | null; getToken: () => Promise<string | null> };

const SIGNED_OUT: MockAuthState = { isSignedIn: false, userId: null, getToken: async () => null };

const { mockUseAuth, mockGetAdminWhoami } = vi.hoisted(() => ({
  mockUseAuth: vi.fn(),
  mockGetAdminWhoami: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: mockUseAuth,
}));

vi.mock("@/lib/adminApi", () => ({
  getAdminWhoami: mockGetAdminWhoami,
}));

function renderNavBar(client: QueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const utils = render(
    <QueryClientProvider client={client}>
      <NavBar />
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

describe("NavBar", () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue(SIGNED_OUT);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    mockGetAdminWhoami.mockReset();
  });

  it("shows the drafting settings gear outside demo mode", () => {
    renderNavBar();
    expect(screen.getByRole("button", { name: /drafting settings/i })).toBeInTheDocument();
  });

  describe("in demo mode", () => {
    beforeEach(() => {
      vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "true");
    });

    it("hides the drafting settings gear", () => {
      renderNavBar();
      expect(screen.queryByRole("button", { name: /drafting settings/i })).not.toBeInTheDocument();
    });
  });

  it("does not show an Admin link for a signed-out visitor", () => {
    renderNavBar();
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
    // No reason to even call the backend for the overwhelming majority
    // of (signed-out) traffic.
    expect(mockGetAdminWhoami).not.toHaveBeenCalled();
  });

  it("does not show an Admin link for a signed-in visitor who isn't allow-listed", async () => {
    mockUseAuth.mockReturnValue({ isSignedIn: true, userId: "user_1", getToken: async () => "faketoken" });
    mockGetAdminWhoami.mockRejectedValue(new Error("403"));
    renderNavBar();

    await waitFor(() => expect(mockGetAdminWhoami).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("shows an Admin link once GET /admin/whoami succeeds", async () => {
    mockUseAuth.mockReturnValue({ isSignedIn: true, userId: "user_1", getToken: async () => "faketoken" });
    mockGetAdminWhoami.mockResolvedValue({ email: "niramayrkelkar@gmail.com" });
    renderNavBar();

    const link = await screen.findByRole("link", { name: "Admin" });
    expect(link).toHaveAttribute("href", "/admin/feedback");
  });

  it("hides the Admin link again after a client-side sign-out, with no stale cached result", async () => {
    // Regression test for a real bug caught during live verification:
    // enabled:false alone does not clear a TanStack Query's previous
    // data, so a fixed "admin-whoami" query key kept showing the link
    // after sign-out until a full page reload. The fix keys the query
    // on userId; this reproduces the exact same QueryClient/rerender
    // shape a client-side sign-out produces (no remount).
    mockUseAuth.mockReturnValue({ isSignedIn: true, userId: "user_1", getToken: async () => "faketoken" });
    mockGetAdminWhoami.mockResolvedValue({ email: "niramayrkelkar@gmail.com" });
    const { client, rerender } = renderNavBar();
    await screen.findByRole("link", { name: "Admin" });

    mockUseAuth.mockReturnValue(SIGNED_OUT);
    rerender(
      <QueryClientProvider client={client}>
        <NavBar />
      </QueryClientProvider>,
    );

    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });
});
