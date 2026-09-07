import DOMPurify from "dompurify";

/**
 * Job descriptions are third-party HTML scraped from 7 different ATS
 * platforms (see CLAUDE.md). Greenhouse stores it entity-escaped
 * (`&lt;p&gt;...`), every other source stores raw HTML. Both carry real
 * semantic structure - headings, `<ul>/<li>` lists, paragraphs - that we
 * want to preserve when rendering, without opening an XSS hole.
 *
 * This unescapes the Greenhouse case, then runs DOMPurify with a
 * deliberately narrow allow-list: block/inline text structure only, no
 * `style`/`class`/`id` (Lever embeds inline `font-size` on nearly every
 * element - dropping it keeps the app's own typography consistent),
 * links forced to open safely in a new tab, and empty spacer nodes
 * dropped.
 */

const ALLOWED_TAGS = [
  "p",
  "br",
  "hr",
  "ul",
  "ol",
  "li",
  "strong",
  "b",
  "em",
  "i",
  "u",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "a",
  "blockquote",
  "div",
  "span",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
];

const ALLOWED_ATTR = ["href", "target", "rel"];

let hookInstalled = false;

function installLinkHook() {
  if (hookInstalled || typeof window === "undefined") return;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.getAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer nofollow");
    }
  });
  hookInstalled = true;
}

/** True when the value looks like entity-escaped HTML (Greenhouse). */
function isEntityEscaped(value: string): boolean {
  return !value.includes("<") && /&lt;\/?[a-z]/i.test(value);
}

function unescapeEntities(value: string): string {
  if (typeof window === "undefined") {
    // Minimal fallback for any non-DOM context.
    return value
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&nbsp;/g, " ")
      .replace(/&amp;/g, "&");
  }
  const el = document.createElement("textarea");
  el.innerHTML = value;
  return el.value;
}

/**
 * Sanitize a scraped job description into HTML that is safe to pass to
 * `dangerouslySetInnerHTML`. Returns an empty string for nullish/blank
 * input or when run without a DOM (SSR) - the job detail view only
 * renders this client-side.
 */
export function sanitizeJobDescription(raw: string | null | undefined): string {
  if (!raw) return "";
  const html = isEntityEscaped(raw) ? unescapeEntities(raw) : raw;
  if (typeof window === "undefined") return "";
  installLinkHook();

  const clean = DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
  });

  // Drop the empty `<p>`/`<div>` (incl. `<p><br></p>`, `<p>&nbsp;</p>`)
  // spacer nodes Greenhouse, Lever and Gem scatter between real
  // paragraphs - the CSS gives real blocks their own spacing.
  return clean
    .replace(/<(p|div)>(?:\s|&nbsp;|<br\s*\/?>)*<\/\1>/gi, "")
    .trim();
}
