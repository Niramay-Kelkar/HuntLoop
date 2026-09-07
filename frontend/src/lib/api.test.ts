import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, getJob, getJobs, getLocations, uploadResume, updateApplicationStatus } from "./api";

/**
 * Covers the fetch-client functions actually worth testing: URL/query-param
 * construction (getJobs' filter params are the real, non-trivial logic
 * here - department/company/min_score/sort are all optional and only
 * appended when set), the shared apiFetch error path, and the multipart
 * upload's deliberate divergence from apiFetch (no manual Content-Type,
 * since the browser must set its own multipart boundary).
 */
describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockFetchOnce(response: { ok: boolean; status?: number; json?: unknown; text?: string }) {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: response.ok,
      status: response.status ?? (response.ok ? 200 : 500),
      json: () => Promise.resolve(response.json),
      text: () => Promise.resolve(response.text ?? ""),
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("getJobs builds a query string with only the params that are set", async () => {
    const fetchMock = mockFetchOnce({ ok: true, json: { items: [], total: 0, limit: 20, offset: 0 } });

    await getJobs({ company: "checkr", sort: "-score" });

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("http://localhost:8000/jobs?");
    expect(url).toContain("company=checkr");
    expect(url).toContain("sort=-score");
    expect(url).not.toContain("min_score");
    expect(url).not.toContain("department");
  });

  it("getJobs includes min_score and department only when explicitly passed", async () => {
    const fetchMock = mockFetchOnce({ ok: true, json: { items: [], total: 0, limit: 20, offset: 0 } });

    await getJobs({ min_score: 0.5, department: "Engineering" });

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("min_score=0.5");
    expect(url).toContain("department=Engineering");
  });

  it("getJobs includes location and salary range params only when passed", async () => {
    const fetchMock = mockFetchOnce({ ok: true, json: { items: [], total: 0, limit: 20, offset: 0 } });

    await getJobs({
      location: ["New York"],
      salary_min: 150000,
      salary_max: 200000,
      salary_unspecified: true,
    });

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("location=New+York");
    expect(url).toContain("salary_min=150000");
    expect(url).toContain("salary_max=200000");
    expect(url).toContain("salary_unspecified=true");
  });

  it("getJobs repeats the location param once per selected value", async () => {
    const fetchMock = mockFetchOnce({ ok: true, json: { items: [], total: 0, limit: 20, offset: 0 } });

    await getJobs({ location: ["New York, NY, United States", "London, United Kingdom"] });

    const url = fetchMock.mock.calls[0][0] as string;
    const params = new URLSearchParams(url.slice(url.indexOf("?") + 1));
    expect(params.getAll("location")).toEqual([
      "New York, NY, United States",
      "London, United Kingdom",
    ]);
  });

  it("getLocations requests the distinct-locations endpoint", async () => {
    const fetchMock = mockFetchOnce({ ok: true, json: ["New York, NY"] });

    await getLocations();

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/jobs/locations", expect.any(Object));
  });

  it("getJobs omits the query string entirely when called with no params", async () => {
    const fetchMock = mockFetchOnce({ ok: true, json: { items: [], total: 0, limit: 20, offset: 0 } });

    await getJobs();

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/jobs", expect.any(Object));
  });

  it("getJob requests the single-job endpoint by id", async () => {
    const fetchMock = mockFetchOnce({ ok: true, json: { id: 42 } });

    await getJob(42);

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/jobs/42", expect.any(Object));
  });

  it("rejects with an ApiError carrying the status and raw body when a request fails", async () => {
    mockFetchOnce({ ok: false, status: 404, text: "job not found" });

    const err = await getJob(999).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.detail).toBe("job not found");
    // The user-facing message stays short - the raw body is not in it.
    expect(err.message).not.toContain("job not found");
  });

  it("rejects with an ApiError (status null) when the server is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    const err = await getJob(1).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBeNull();
  });

  it("PATCHes application status with a JSON body", async () => {
    const fetchMock = mockFetchOnce({
      ok: true,
      json: { job_posting_id: 1, status: "applied", applied_at: null, status_updated_at: "now", notes: null },
    });

    await updateApplicationStatus(1, { status: "applied" });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/jobs/1/application",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ status: "applied" }) }),
    );
  });

  it("uploadResume sends multipart form data without a manual Content-Type header", async () => {
    const fetchMock = mockFetchOnce({
      ok: true,
      json: { id: 1, version_number: 1, uploaded_at: "now", is_active: true, text_preview: "" },
    });

    const file = new File(["resume text"], "resume.pdf", { type: "application/pdf" });
    await uploadResume(file);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8000/resumes/upload");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    // apiFetch's default JSON header must NOT be present here - a manual
    // Content-Type would strip the multipart boundary the browser sets.
    expect(init.headers).toBeUndefined();
  });
});
