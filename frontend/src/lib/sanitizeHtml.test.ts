import { describe, expect, it } from "vitest";

import { sanitizeJobDescription } from "./sanitizeHtml";

describe("sanitizeJobDescription", () => {
  it("returns an empty string for nullish or blank input", () => {
    expect(sanitizeJobDescription(null)).toBe("");
    expect(sanitizeJobDescription(undefined)).toBe("");
    expect(sanitizeJobDescription("")).toBe("");
  });

  it("unescapes entity-escaped Greenhouse HTML into real structure", () => {
    const raw =
      "&lt;p&gt;&lt;strong&gt;What you&#39;ll do&lt;/strong&gt;&lt;/p&gt;\n&lt;ul&gt;&lt;li&gt;Ship code&lt;/li&gt;&lt;li&gt;Review PRs&lt;/li&gt;&lt;/ul&gt;";
    const out = sanitizeJobDescription(raw);
    expect(out).toContain("<strong>What you'll do</strong>");
    expect(out).toContain("<ul>");
    expect(out).toContain("<li>Ship code</li>");
    expect(out).not.toContain("&lt;");
  });

  it("preserves real HTML structure from raw-HTML sources", () => {
    const raw = "<div><strong>About</strong></div><ul><li>One</li><li>Two</li></ul>";
    const out = sanitizeJobDescription(raw);
    expect(out).toContain("<ul>");
    expect(out).toContain("<li>Two</li>");
    expect(out).toContain("<strong>About</strong>");
  });

  it("strips script tags and event-handler attributes", () => {
    const out = sanitizeJobDescription(
      '<p>Hi</p><script>alert(1)</script><img src=x onerror="alert(1)">',
    );
    expect(out).not.toContain("<script");
    expect(out).not.toContain("onerror");
    expect(out).not.toContain("alert(1)");
    expect(out).toContain("<p>Hi</p>");
  });

  it("removes inline style and class attributes", () => {
    const out = sanitizeJobDescription(
      '<div style="font-size: 18px" class="postings-x"><b>Header</b></div>',
    );
    expect(out).not.toContain("style=");
    expect(out).not.toContain("class=");
    expect(out).toContain("<b>Header</b>");
  });

  it("forces links to open safely in a new tab and drops javascript: URLs", () => {
    const out = sanitizeJobDescription(
      '<a href="https://example.com/apply">Apply</a> <a href="javascript:alert(1)">x</a>',
    );
    expect(out).toContain('href="https://example.com/apply"');
    expect(out).toContain('target="_blank"');
    expect(out).toContain("noopener");
    expect(out).not.toContain("javascript:");
  });

  it("drops empty paragraph and div spacer nodes", () => {
    const out = sanitizeJobDescription(
      "<p>First</p><p>&nbsp;</p><div><br></div><p>  </p><p>Second</p>",
    );
    expect(out).toBe("<p>First</p><p>Second</p>");
  });

  it("keeps entity-escaped ampersands readable", () => {
    const out = sanitizeJobDescription("&lt;p&gt;A &amp;amp; B&lt;/p&gt;");
    expect(out).toContain("A &amp; B");
  });
});
