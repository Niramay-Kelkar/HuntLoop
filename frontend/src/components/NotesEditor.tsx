"use client";

import { useEffect, useRef, useState } from "react";

import { useApplicationNotesMutation } from "@/hooks/useApplicationStatus";

/**
 * A plain-text note for one application, wired to the shared
 * useApplicationNotesMutation (PATCH /jobs/{id}/application with just
 * `notes`, optimistic + rollback + toast). Saves on blur, and only when
 * the text actually changed - deliberately not a rich-text editor.
 */
export function NotesEditor({
  jobId,
  notes,
  placeholder = "Add a note — recruiter name, next step, salary discussed…",
  rows = 2,
}: {
  jobId: number;
  notes: string | null;
  placeholder?: string;
  rows?: number;
}) {
  const mutation = useApplicationNotesMutation();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState(notes ?? "");
  const lastSaved = useRef(notes ?? "");

  // Keep in sync when the underlying note changes elsewhere (e.g. another
  // view saved it), but don't clobber an in-progress edit.
  useEffect(() => {
    if (document.activeElement !== textareaRef.current) {
      setValue(notes ?? "");
      lastSaved.current = notes ?? "";
    }
  }, [notes]);

  function save() {
    const next = value.trim();
    if (next === lastSaved.current.trim()) return;
    lastSaved.current = next;
    mutation.mutate({ jobId, notes: next });
  }

  return (
    <div className="flex flex-col gap-1">
      <textarea
        ref={textareaRef}
        value={value}
        rows={rows}
        placeholder={placeholder}
        aria-label="Application note"
        onChange={(e) => setValue(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            textareaRef.current?.blur();
          }
        }}
        className="w-full resize-y rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[13px] leading-relaxed text-text placeholder:text-text-faintest focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent"
      />
      {mutation.isPending && <span className="font-mono text-[10px] text-text-faintest">Saving…</span>}
    </div>
  );
}
