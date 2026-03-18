interface BadgeProps {
  label: string;
  variant: "green" | "red" | "blue" | "purple" | "gray" | "amber";
}

const variantClasses: Record<BadgeProps["variant"], string> = {
  green: "bg-green-100 text-green-800",
  red: "bg-red-100 text-red-800",
  blue: "bg-blue-100 text-blue-800",
  purple: "bg-purple-100 text-purple-800",
  gray: "bg-gray-100 text-gray-800",
  amber: "bg-amber-100 text-amber-800",
};

export default function Badge({ label, variant }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${variantClasses[variant]}`}
    >
      {label}
    </span>
  );
}

export function DecisionBadge({ decision }: { decision: string | null }) {
  if (!decision) return null;
  return (
    <Badge
      label={decision === "approve" ? "Approve" : "Request Changes"}
      variant={decision === "approve" ? "green" : "red"}
    />
  );
}

export function TypeBadge({ type }: { type: string }) {
  return (
    <Badge
      label={type === "lightweight" ? "Lightweight" : "Full Review"}
      variant={type === "lightweight" ? "blue" : "purple"}
    />
  );
}

export function StageBadge({ stage }: { stage: string }) {
  const colors: Record<string, BadgeProps["variant"]> = {
    claude: "amber",
    codex: "blue",
    gemini: "green",
    reconcile: "purple",
    lightweight: "blue",
  };
  return <Badge label={stage} variant={colors[stage] || "gray"} />;
}
