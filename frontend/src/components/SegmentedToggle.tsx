export function SegmentedToggle<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex border border-border-strong">
      {options.map((opt, i) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`px-3 py-1.5 font-mono text-xs font-medium uppercase tracking-[0.04em] ${
            i > 0 ? "border-l border-border-strong" : ""
          } ${
            value === opt.value
              ? "bg-accent text-white"
              : "bg-surface text-text-subtle hover:text-text"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
