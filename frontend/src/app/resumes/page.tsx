"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { activateResume, getResumes, uploadResume } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { useToast } from "@/components/Toast";
import { formatDate } from "@/lib/theme";
import { isDemoMode } from "@/lib/demoMode";

export default function ResumesPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const resumes = useQuery({
    queryKey: ["resumes"],
    queryFn: getResumes,
  });

  // A resume swap changes every job's live match_score (computed at
  // query time against whichever version is_active - see
  // huntloop.api.routers.jobs) and resets matched_skills/missing_skills
  // for reprocessing, so both jobs and dashboard stats need to reflect
  // the new active version, not just the resumes list itself.
  function invalidateEverythingAffectedByActiveResume() {
    queryClient.invalidateQueries({ queryKey: ["resumes"] });
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
    queryClient.invalidateQueries({ queryKey: ["job"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] });
  }

  const uploadMutation = useMutation({
    mutationFn: uploadResume,
    onSuccess: (resume) => {
      invalidateEverythingAffectedByActiveResume();
      showToast(`Version ${resume.version_number} uploaded and is now active`, "success");
    },
    onError: (error) => {
      showToast(`Upload failed${error instanceof Error ? `: ${error.message}` : ""}`, "error");
    },
  });

  const activateMutation = useMutation({
    mutationFn: activateResume,
    onSuccess: (resume) => {
      invalidateEverythingAffectedByActiveResume();
      showToast(`Version ${resume.version_number} is now active`, "success");
    },
    onError: (error) => {
      showToast(`Activation failed${error instanceof Error ? `: ${error.message}` : ""}`, "error");
    },
  });

  function handleFile(file: File | undefined | null) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      showToast("Only PDF files are supported.", "error");
      return;
    }
    uploadMutation.mutate(file);
  }

  const isUploading = uploadMutation.isPending;

  return (
    <main className="flex flex-1 flex-col gap-4">
      <div className="border-b border-border pb-4">
        <h1 className="text-xl font-bold tracking-tight text-text">Resume</h1>
        <p className="mt-1 text-sm text-text-subtle">
          The active version is what every job is scored against.
        </p>
      </div>

      <div className="mx-auto flex w-full max-w-[440px] flex-col gap-4">
        {/* upload */}
        {isDemoMode() ? (
          <div
            data-testid="resume-upload-disabled"
            className="border-2 border-dashed border-border-strong bg-surface p-6 text-center"
          >
            <div className="text-sm font-semibold text-text">Upload disabled in this demo</div>
            <div className="mx-auto mt-1 max-w-[320px] text-xs text-text-faintest">
              Run HuntLoop locally to upload and score your own resume.
            </div>
          </div>
        ) : (
          <div
            className={`border-2 border-dashed p-6 text-center transition-colors ${
              isDragOver ? "border-accent bg-accent-soft" : "border-border-strong bg-surface"
            } ${isUploading ? "pointer-events-none opacity-70" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragOver(true);
            }}
            onDragLeave={() => setIsDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragOver(false);
              handleFile(e.dataTransfer.files[0]);
            }}
          >
            {isUploading ? (
              <>
                <div className="mx-auto mb-3 h-10 w-10 animate-spin rounded-full border-2 border-accent-soft border-t-accent" />
                <div className="text-sm font-semibold text-text">Uploading &amp; processing…</div>
                <div className="mx-auto mt-1 max-w-[280px] text-xs text-text-faintest">
                  Extracting text and computing an embedding for this resume, this can take a few seconds.
                </div>
              </>
            ) : (
              <>
                <div className="mx-auto mb-3 grid h-10 w-10 place-items-center border border-border-strong bg-accent-soft">
                  <span className="font-mono text-base font-semibold text-accent">PDF</span>
                </div>
                <div className="text-sm font-semibold text-text">Upload a new version</div>
                <div className="mb-3.5 mt-1 text-xs text-text-faintest">Drop a PDF or browse</div>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover"
                >
                  Choose file
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,application/pdf"
                  className="hidden"
                  onChange={(e) => {
                    handleFile(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />
              </>
            )}
          </div>
        )}

        {/* version history */}
        <div className="border border-border bg-surface p-5">
          <h3 className="mb-3 text-sm font-semibold text-text">Version history</h3>

          {resumes.isPending && (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="h-20 animate-pulse border border-border bg-surface-alt" />
              ))}
            </div>
          )}

          {resumes.isError && (
            <ErrorState
              error={resumes.error}
              onRetry={() => resumes.refetch()}
              resourceLabel="your resumes"
              compact
            />
          )}

          {resumes.isSuccess && resumes.data.length === 0 && (
            <EmptyState
              icon="◦"
              title="No resume uploaded yet"
              description="Upload a PDF above — it becomes the active version every job is scored against."
              compact
            />
          )}

          {resumes.isSuccess && resumes.data.length > 0 && (
            <div className="flex flex-col gap-2.5">
              {resumes.data.map((version) => (
                <div
                  key={version.id}
                  className="border p-3.5"
                  style={
                    version.is_active
                      ? { borderColor: "var(--color-good-border)", backgroundColor: "var(--color-good-bg)" }
                      : { borderColor: "var(--color-border)", backgroundColor: "var(--color-surface)" }
                  }
                >
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="font-mono text-sm font-bold text-text">v{version.version_number}</span>
                    {version.is_active && (
                      <span
                        className="border px-1.5 py-px font-mono text-2xs uppercase tracking-[0.06em]"
                        style={{
                          borderColor: "var(--color-good-border)",
                          color: "var(--color-good)",
                        }}
                      >
                        Active
                      </span>
                    )}
                    <span className="ml-auto font-mono text-2xs text-text-faintest">
                      {formatDate(version.uploaded_at)}
                    </span>
                  </div>
                  <p className="line-clamp-2 text-xs leading-relaxed text-text-faint">{version.text_preview}</p>
                  {!version.is_active && !isDemoMode() && (
                    <button
                      type="button"
                      disabled={activateMutation.isPending}
                      onClick={() => activateMutation.mutate(version.id)}
                      className="mt-2.5 w-full border border-border-strong bg-surface-alt py-1.5 text-xs font-semibold text-text hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {activateMutation.isPending && activateMutation.variables === version.id
                        ? "Activating…"
                        : "Make active"}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
