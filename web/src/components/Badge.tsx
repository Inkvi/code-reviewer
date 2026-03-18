interface BadgeProps {
  label: string;
  variant: "green" | "red" | "blue" | "purple" | "gray" | "amber" | "cyan";
}

const variantClasses: Record<BadgeProps["variant"], string> = {
  green:
    "bg-emerald-500/15 text-emerald-400 border border-emerald-500/25",
  red: "bg-rose-500/15 text-rose-400 border border-rose-500/25",
  blue: "bg-blue-500/15 text-blue-400 border border-blue-500/25",
  purple:
    "bg-purple-500/15 text-purple-400 border border-purple-500/25",
  gray: "bg-gray-500/15 text-gray-400 border border-gray-500/25",
  amber:
    "bg-amber-500/15 text-amber-400 border border-amber-500/25",
  cyan: "bg-cyan-500/15 text-cyan-400 border border-cyan-500/25",
};

export default function Badge({ label, variant }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium font-mono tracking-wide ${variantClasses[variant]}`}
    >
      {label}
    </span>
  );
}

export function DecisionBadge({ decision }: { decision: string | null }) {
  if (!decision) return null;
  const isApprove = decision === "approve";
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold tracking-wide ${
        isApprove
          ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
          : "bg-rose-500/15 text-rose-400 border border-rose-500/30"
      }`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${
          isApprove ? "bg-emerald-400" : "bg-rose-400"
        }`}
      />
      {isApprove ? "Approve" : "Request Changes"}
    </span>
  );
}

export function TypeBadge({ type }: { type: string }) {
  const isLight = type === "lightweight";
  return (
    <Badge
      label={isLight ? "Lightweight" : "Full Review"}
      variant={isLight ? "cyan" : "purple"}
    />
  );
}

export function StageBadge({ stage }: { stage: string }) {
  const colors: Record<string, BadgeProps["variant"]> = {
    claude: "amber",
    codex: "blue",
    gemini: "green",
    reconcile: "purple",
    lightweight: "cyan",
  };
  return <Badge label={stage} variant={colors[stage] || "gray"} />;
}
