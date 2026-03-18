import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  fetchPRDetail,
  fetchVersionDetail,
  type PRDetailData,
  type VersionDetailData,
} from "../api";
import { DecisionBadge, TypeBadge } from "../components/Badge";
import MarkdownView from "../components/MarkdownView";

interface Props {
  isHistorical?: boolean;
}

type DetailData = PRDetailData | VersionDetailData;

/* ── per-reviewer visual identity ── */

interface ReviewerTheme {
  label: string;
  color: string;
  activeText: string;
  hoverText: string;
  underline: string;
  icon: React.ReactNode;
}

const ICON_CLS = "w-3.5 h-3.5 shrink-0";

function strokeIcon(d: string, strokeWidth = 2) {
  return (
    <svg
      className={ICON_CLS}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d} />
    </svg>
  );
}

/* Anthropic/Claude mark — stylized starburst */
const ClaudeIcon = (
  <svg className={ICON_CLS} viewBox="0 0 24 24" fill="currentColor">
    <path d="M16.98 5.59L14.84 12l2.14 6.41a.6.6 0 01-.57.79h-3.04a.6.6 0 01-.57-.41L12 15.5l-.8 3.29a.6.6 0 01-.57.41H7.59a.6.6 0 01-.57-.79L9.16 12 7.02 5.59a.6.6 0 01.57-.79h3.04a.6.6 0 01.57.41L12 8.5l.8-3.29a.6.6 0 01.57-.41h3.04a.6.6 0 01.57.79z" />
  </svg>
);

/* OpenAI/Codex mark — hexagonal knot */
const CodexIcon = (
  <svg className={ICON_CLS} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
    <path
      d="M12 2.5L3.5 7.4v9.2L12 21.5l8.5-4.9V7.4L12 2.5z"
      strokeLinejoin="round"
    />
    <path d="M12 2.5v6.3M3.5 7.4l5.4 3.1m3.1 1.8L3.5 16.6m8.5 4.9v-6.3m8.5-4.6l-5.4 3.1m-3.1-1.8L20.5 7.4" strokeLinecap="round" />
    <circle cx="12" cy="12.3" r="1.5" fill="currentColor" stroke="none" />
  </svg>
);

/* Google Gemini mark — four-pointed curved star */
const GeminiIcon = (
  <svg className={ICON_CLS} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2C12 7.1 7.1 12 2 12c5.1 0 10 4.9 10 10 0-5.1 4.9-10 10-10-5.1 0-10-4.9-10-10z" />
  </svg>
);

const REVIEWER_THEMES: Record<string, ReviewerTheme> = {
  final: {
    label: "Final Review",
    color: "accent-blue",
    activeText: "text-accent-blue",
    hoverText: "hover:text-blue-300",
    underline: "bg-accent-blue",
    icon: strokeIcon(
      "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
    ),
  },
  claude: {
    label: "Claude",
    color: "amber",
    activeText: "text-amber-400",
    hoverText: "hover:text-amber-300",
    underline: "bg-amber-400",
    icon: ClaudeIcon,
  },
  codex: {
    label: "Codex",
    color: "blue",
    activeText: "text-blue-400",
    hoverText: "hover:text-blue-300",
    underline: "bg-blue-400",
    icon: CodexIcon,
  },
  gemini: {
    label: "Gemini",
    color: "emerald",
    activeText: "text-emerald-400",
    hoverText: "hover:text-emerald-300",
    underline: "bg-emerald-400",
    icon: GeminiIcon,
  },
  reconcile: {
    label: "Reconciled",
    color: "purple",
    activeText: "text-purple-400",
    hoverText: "hover:text-purple-300",
    underline: "bg-purple-400",
    icon: strokeIcon(
      "M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5",
    ),
  },
  lightweight: {
    label: "Lightweight",
    color: "cyan",
    activeText: "text-cyan-400",
    hoverText: "hover:text-cyan-300",
    underline: "bg-cyan-400",
    icon: strokeIcon("M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75"),
  },
};

function getTheme(stage: string): ReviewerTheme {
  return (
    REVIEWER_THEMES[stage] || {
      label: stage,
      color: "gray",
      activeText: "text-gray-400",
      hoverText: "hover:text-gray-300",
      underline: "bg-gray-400",
      icon: strokeIcon("M12 6.75a.75.75 0 110-1.5.75.75 0 010 1.5zM12 12.75a.75.75 0 110-1.5.75.75 0 010 1.5zM12 18.75a.75.75 0 110-1.5.75.75 0 010 1.5z"),
    }
  );
}

/* ── collapsible prompt disclosure ── */

function PromptDisclosure({ content }: { content: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mb-5 rounded-lg border border-surface-border overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 w-full px-4 py-2.5 text-sm font-medium text-gray-400 hover:text-gray-200 bg-surface-3/50 hover:bg-surface-3 transition-colors"
      >
        <svg
          className={`w-3.5 h-3.5 transition-transform duration-200 ${open ? "rotate-90" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M8.25 4.5l7.5 7.5-7.5 7.5" />
        </svg>
        Prompt
      </button>
      {open && (
        <div className="px-4 py-4 border-t border-surface-border bg-surface-1/50 animate-fade-in">
          <MarkdownView content={content} />
        </div>
      )}
    </div>
  );
}

/* ── main component ── */

export default function PRDetail({ isHistorical }: Props) {
  const { org, repo, number, version } = useParams<{
    org: string;
    repo: string;
    number: string;
    version: string;
  }>();
  const [data, setData] = useState<DetailData | null>(null);
  const [activeTab, setActiveTab] = useState<string>("final");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!org || !repo || !number) return;
    const num = parseInt(number, 10);
    const promise =
      isHistorical && version
        ? fetchVersionDetail(org, repo, num, version)
        : fetchPRDetail(org, repo, num);
    promise
      .then((d) => {
        setData(d);
        setActiveTab("final");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [org, repo, number, version, isHistorical]);

  if (loading)
    return (
      <div className="flex items-center gap-3 text-gray-500 py-12">
        <div className="w-4 h-4 border-2 border-accent-blue/30 border-t-accent-blue rounded-full animate-spin" />
        Loading...
      </div>
    );
  if (error)
    return (
      <p className="text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-lg px-4 py-3">
        Error: {error}
      </p>
    );
  if (!data) return <p className="text-gray-500">Not found.</p>;

  const stageContents = data.stage_contents;
  const reviewStages = data.stages.filter((s) => !s.endsWith(".prompt"));
  const tabs = [
    { id: "final", theme: getTheme("final") },
    ...reviewStages.map((s) => ({ id: s, theme: getTheme(s) })),
  ];

  const activeContent =
    activeTab === "final"
      ? data.final_review
      : stageContents[activeTab] || "No content available.";

  // Find corresponding prompt for the active tab
  const promptKey = activeTab === "final" ? null : `${activeTab}.prompt`;
  const promptContent = promptKey ? stageContents[promptKey] : null;

  return (
    <div className="animate-fade-in-up">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-2xl font-bold text-gray-100 tracking-tight">
          <span className="text-gray-500 font-mono">#</span>
          {number}
        </h1>
        <DecisionBadge decision={data.decision} />
        <TypeBadge type={data.review_type} />
      </div>

      {isHistorical && version && (
        <p className="text-sm font-mono text-gray-500 mb-6">{version}</p>
      )}

      {!isHistorical && "versions" in data && data.versions.length > 0 && (
        <div className="mb-6">
          <Link
            to={`/${org}/${repo}/${number}/history`}
            className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-accent-blue transition-colors"
          >
            <svg
              className="w-3.5 h-3.5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            {data.versions.length} historical version
            {data.versions.length !== 1 ? "s" : ""}
          </Link>
        </div>
      )}

      <div className="bg-surface-2 rounded-xl border border-surface-border overflow-hidden">
        {/* Tab bar */}
        <div className="border-b border-surface-border px-4">
          <div className="flex gap-0.5 overflow-x-auto">
            {tabs.map((tab) => {
              const active = activeTab === tab.id;
              const t = tab.theme;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`relative flex items-center gap-2 px-3.5 py-3 text-sm font-medium whitespace-nowrap transition-colors duration-150 ${
                    active
                      ? t.activeText
                      : `text-gray-500 ${t.hoverText}`
                  }`}
                >
                  {t.icon}
                  {t.label}
                  {active && (
                    <span
                      className={`absolute bottom-0 left-1.5 right-1.5 h-0.5 rounded-full ${t.underline}`}
                    />
                  )}
                </button>
              );
            })}
          </div>
        </div>
        {/* Content */}
        <div className="p-6 animate-fade-in" key={activeTab}>
          {promptContent && <PromptDisclosure content={promptContent} />}
          <MarkdownView content={activeContent} />
        </div>
      </div>
    </div>
  );
}
