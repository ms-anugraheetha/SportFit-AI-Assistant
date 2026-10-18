import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import ChatPage from "./Chat";
import StaffApp from "./Staff";

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown };

function mockFetch(handler: Handler) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const { status = 200, body = {} } = handler(url, init);
      return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
    }),
  );
  return calls;
}

const META = {
  statuses: ["received", "under_review", "in_progress", "completed", "cancelled"],
  status_labels: { received: "Received", under_review: "Under review", in_progress: "In progress", completed: "Completed", cancelled: "Cancelled" },
  disclaimer: "Independent academic prototype disclaimer from the server.",
  suggested_prompts: ["What repair services are available?", "Submit a repair request."],
};

beforeEach(() => {
  sessionStorage.clear();
  window.history.pushState({}, "", "/");
});
afterEach(() => vi.unstubAllGlobals());

describe("customer chat", () => {
  it("shows the prototype disclaimer, privacy notice and server-provided prompts", async () => {
    mockFetch((url) => (url === "/api/meta" ? { body: META } : { status: 404 }));
    render(<ChatPage />);
    expect(screen.getAllByText(/independent academic prototype/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/don't share passwords/i)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Submit a repair request." })).toBeInTheDocument();
  });

  it("sends a message, shows the reply, the reference and follow-up actions, and reuses the conversation id", async () => {
    const calls = mockFetch((url, init) => {
      if (url === "/api/meta") return { body: META };
      const sent = JSON.parse(String(init?.body));
      if (sent.message === "confirm")
        return {
          body: { conversation_id: "c1", state: "request_created", reply: "Recorded in the demo.", suggested_actions: [], request_reference: "SFD-ABCD-EFGH-JKMN", sources: [], safety_notice: [], degraded: false },
        };
      return {
        body: { conversation_id: "c1", state: "awaiting_submission_confirmation", reply: "Please check this summary.", suggested_actions: ["confirm", "cancel"], request_reference: null, sources: [], safety_notice: ["Stop using the machine."], degraded: false },
      };
    });
    const user = userEvent.setup();
    render(<ChatPage />);
    await user.type(screen.getByLabelText("Your message"), "Submit a repair request");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Please check this summary.")).toBeInTheDocument();
    expect(screen.getByText("Stop using the machine.")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "confirm" }));
    expect(await screen.findByText("SFD-ABCD-EFGH-JKMN")).toBeInTheDocument();

    const chatCalls = calls.filter((c) => c.url === "/api/chat").map((c) => JSON.parse(String(c.init?.body)));
    expect(chatCalls[0].conversation_id).toBeNull();
    expect(chatCalls[1].conversation_id).toBe("c1");
  });

  it("shows an error message and lets the customer retry", async () => {
    let fail = true;
    mockFetch((url) => {
      if (url === "/api/meta") return { body: META };
      if (fail) return { status: 500, body: { detail: "Internal server error" } };
      return { body: { conversation_id: "c2", state: "general", reply: "Back online.", suggested_actions: [], request_reference: null, sources: [], safety_notice: [], degraded: false } };
    });
    const user = userEvent.setup();
    render(<ChatPage />);
    await user.type(screen.getByLabelText("Your message"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Internal server error")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try sending again" }));
    expect(await screen.findByText("Back online.")).toBeInTheDocument();
  });

  it("does not send an empty message", async () => {
    mockFetch(() => ({ body: META }));
    render(<ChatPage />);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});

describe("staff dashboard", () => {
  const SESSION = { username: "staff", csrf_token: "csrf-1", expires_at: "2026-10-19T00:00:00Z" };
  const ROW = { request_reference: "SFD-AAAA-BBBB-CCCC", created_at: "2026-10-18T10:00:00Z", customer_name: "Test Customer", equipment_type: "Treadmill", problem_summary: "Belt stops", status: "received" };
  const DETAIL = {
    ...ROW, email: "customer@example.com", phone: null, service_area: "Mahboula", equipment_brand: null, equipment_model: null, serial_number: null,
    problem_description: "Belt stops after a few minutes.", preferred_contact_method: "email", preferred_appointment_time: null, updated_at: "2026-10-18T10:00:00Z", history: [],
  };

  it("shows the login form when there is no session and reports bad credentials", async () => {
    window.history.pushState({}, "", "/staff");
    mockFetch((url) => {
      if (url === "/api/meta") return { body: META };
      if (url === "/api/staff/me") return { status: 401, body: { detail: "Staff login required." } };
      if (url === "/api/staff/login") return { status: 401, body: { detail: "Invalid username or password." } };
      return { status: 404 };
    });
    const user = userEvent.setup();
    render(<StaffApp />);
    await user.type(await screen.findByLabelText("Username"), "staff");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password.");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("lists requests with real counts and updates status only after the server confirms, sending the CSRF token", async () => {
    const calls = mockFetch((url, init) => {
      if (url === "/api/meta") return { body: META };
      if (url === "/api/staff/me") return { body: SESSION };
      if (url === "/api/staff/summary") return { body: { total: 1, counts: { received: 1, under_review: 0, in_progress: 0, completed: 0, cancelled: 0 } } };
      if (url.startsWith("/api/staff/service-requests?")) return { body: { items: [ROW], page: 1, page_size: 10, total: 1, total_pages: 1 } };
      if (url.endsWith("/status") && init?.method === "PATCH") return { body: { ...DETAIL, status: "in_progress", history: [{ old_status: "received", new_status: "in_progress", changed_by: "staff", changed_at: "2026-10-18T11:00:00Z" }] } };
      if (url.startsWith("/api/staff/service-requests/")) return { body: DETAIL };
      return { status: 404 };
    });
    const user = userEvent.setup();
    render(<StaffApp />);
    const table = await screen.findByRole("table");
    expect(await within(table).findByText("Test Customer")).toBeInTheDocument();
    expect(screen.getByText("All requests").previousSibling).toHaveTextContent("1");

    await user.click(screen.getByRole("button", { name: /Open request SFD-AAAA-BBBB-CCCC/ }));
    expect(await screen.findByText("customer@example.com")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("New status"), "in_progress");
    await user.click(screen.getByRole("button", { name: "Update status" }));
    expect(await screen.findByText(/Status updated to "In progress"/)).toBeInTheDocument();

    const patch = calls.find((c) => c.init?.method === "PATCH")!;
    expect((patch.init!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(String(patch.init!.body))).toEqual({ status: "in_progress" });
  });

  it("shows an empty state, and falls back to the login screen if the session ends", async () => {
    let expired = false;
    mockFetch((url) => {
      if (url === "/api/meta") return { body: META };
      if (url === "/api/staff/me") return { body: SESSION };
      if (expired) return { status: 401, body: { detail: "Staff login required." } };
      if (url === "/api/staff/summary") return { body: { total: 0, counts: { received: 0, under_review: 0, in_progress: 0, completed: 0, cancelled: 0 } } };
      if (url.startsWith("/api/staff/service-requests?")) return { body: { items: [], page: 1, page_size: 10, total: 0, total_pages: 1 } };
      return { status: 404 };
    });
    const user = userEvent.setup();
    render(<StaffApp />);
    expect(await screen.findByText(/No requests have been recorded yet/)).toBeInTheDocument();
    expired = true;
    await user.type(screen.getByLabelText("Search"), "abc");
    await waitFor(() => expect(screen.getByText(/Your session has ended/)).toBeInTheDocument(), { timeout: 3000 });
  });
});
