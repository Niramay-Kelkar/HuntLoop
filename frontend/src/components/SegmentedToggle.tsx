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
    <div className="flex overflow-hidden rounded-lg border border-border-strong bg-surface">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`px-3.5 py-2 font-mono text-xs font-semibold ${
            value === opt.value ? "bg-accent text-white" : "text-text-subtle hover:text-text"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
