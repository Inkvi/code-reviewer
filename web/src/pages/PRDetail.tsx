import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  fetchPRDetail,
  fetchVersionDetail,
  type ConversationEvent,
  type PRDetailData,
  type ReviewMeta,
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

/* ── conversation steps disclosure ── */

function ConversationStep({ event, index }: { event: ConversationEvent; index: number }) {
  const type = event.type as string;

  if (type === "assistant") {
    const msg = event.message as { content?: Array<Record<string, unknown>> } | undefined;
    const blocks = msg?.content || [];
    return (
      <div className="flex gap-3">
        <div className="flex flex-col items-center">
          <div className="w-6 h-6 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center text-xs font-bold shrink-0">
            A
          </div>
          <div className="w-px flex-1 bg-surface-border/50 mt-1" />
        </div>
        <div className="flex-1 min-w-0 pb-4">
          <span className="text-xs font-medium text-amber-400">Assistant</span>
          <span className="text-xs text-gray-600 ml-2">#{index + 1}</span>
          <div className="mt-1.5 space-y-2">
            {blocks.map((block, i) => {
              const bt = block.type as string;
              if (bt === "text") {
                const text = block.text as string;
                return (
                  <div key={i} className="text-xs text-gray-300 whitespace-pre-wrap break-words leading-relaxed">
                    {text.length > 500 ? text.slice(0, 500) + "…" : text}
                  </div>
                );
              }
              if (bt === "tool_use") {
                return (
                  <div key={i} className="rounded bg-surface-3/60 border border-surface-border/50 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <svg className="w-3 h-3 text-blue-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.384-3.19a.75.75 0 010-1.286l5.383-3.19A.75.75 0 0112 8.02v6.96a.75.75 0 01-.58.52z" />
                      </svg>
                      <span className="text-xs font-mono text-blue-400">{block.name as string}</span>
                    </div>
                    {block.input !== undefined && (
                      <pre className="mt-1.5 text-[10px] text-gray-500 font-mono overflow-x-auto max-h-24 leading-tight">
                        {JSON.stringify(block.input, null, 2)}
                      </pre>
                    )}
                  </div>
                );
              }
              if (bt === "tool_result") {
                const content = block.content as string;
                return (
                  <div key={i} className="rounded bg-surface-3/40 border border-surface-border/30 px-3 py-2">
                    <span className="text-[10px] text-gray-500">tool result</span>
                    <div className="text-[10px] text-gray-500 font-mono mt-0.5 max-h-16 overflow-hidden">
                      {content && content.length > 200 ? content.slice(0, 200) + "…" : content}
                    </div>
                  </div>
                );
              }
              if (bt === "thinking") {
                return (
                  <div key={i} className="rounded bg-purple-500/5 border border-purple-500/20 px-3 py-2">
                    <span className="text-[10px] text-purple-400">thinking</span>
                    <div className="text-[10px] text-gray-500 mt-0.5 max-h-16 overflow-hidden whitespace-pre-wrap">
                      {(block.thinking as string || "").slice(0, 200)}
                      {(block.thinking as string || "").length > 200 ? "…" : ""}
                    </div>
                  </div>
                );
              }
              return (
                <div key={i} className="text-[10px] text-gray-600 font-mono">
                  {JSON.stringify(block)}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  if (type === "result") {
    const result = event.result as string;
    return (
      <div className="flex gap-3">
        <div className="w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center shrink-0">
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
          </svg>
        </div>
        <div className="flex-1 min-w-0 pb-2">
          <span className="text-xs font-medium text-emerald-400">Result</span>
          {result && (
            <div className="mt-1 text-xs text-gray-400 max-h-20 overflow-hidden whitespace-pre-wrap">
              {result.length > 300 ? result.slice(0, 300) + "…" : result}
            </div>
          )}
        </div>
      </div>
    );
  }

  // Generic Codex event rendering
  const eventType = type || "unknown";
  const item = event.item as Record<string, unknown> | undefined;
  return (
    <div className="flex gap-3">
      <div className="w-6 h-6 rounded-full bg-gray-700/50 text-gray-500 flex items-center justify-center text-[9px] font-mono shrink-0">
        {index + 1}
      </div>
      <div className="flex-1 min-w-0 pb-3">
        <span className="text-[10px] font-mono text-gray-500">{eventType}</span>
        {item?.type === "agent_message" && typeof item.text === "string" && (
          <div className="mt-1 text-xs text-gray-400 whitespace-pre-wrap max-h-20 overflow-hidden">
            {item.text.slice(0, 300)}
          </div>
        )}
        {item?.type === "function_call" && (
          <div className="mt-1 flex items-center gap-1.5">
            <svg className="w-3 h-3 text-blue-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.384-3.19a.75.75 0 010-1.286l5.383-3.19A.75.75 0 0112 8.02v6.96a.75.75 0 01-.58.52z" />
            </svg>
            <span className="text-[10px] font-mono text-blue-400">{item.name as string || "tool"}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function ConversationSteps({ events }: { events: ConversationEvent[] }) {
  const [open, setOpen] = useState(false);
  const stepCount = events.length;

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
        Steps
        <span className="text-xs text-gray-600 font-normal">({stepCount})</span>
      </button>
      {open && (
        <div className="px-4 py-4 border-t border-surface-border bg-surface-1/50 animate-fade-in max-h-[600px] overflow-y-auto">
          {events.map((event, i) => (
            <ConversationStep key={i} event={event} index={i} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── review metadata panel ── */

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-gray-500 text-xs">{label}</span>
      <span className="text-gray-300 text-xs font-mono">{value}</span>
    </div>
  );
}

function ReviewMetaPanel({ meta }: { meta: ReviewMeta }) {
  const [open, setOpen] = useState(false);

  type ReviewerInfo = { model?: string; backend?: string; status?: string; duration_seconds?: number; tokens?: { input: number; output: number; cost_usd?: number } };
  type TriggerInfo = { type?: string; by?: string; at?: string; force?: boolean };
  type TokenInfo = { input: number; output: number; cost_usd?: number };
  const reviewers = meta.reviewers as Record<string, ReviewerInfo> | undefined;
  const trigger = meta.trigger as TriggerInfo | undefined;
  const totalTokens = meta.total_tokens as TokenInfo | undefined;
  const changedFiles = meta.changed_files as string[] | undefined;

  const items: { label: string; value: string }[] = [];

  if (meta.review_type) items.push({ label: "Type", value: String(meta.review_type) });
  if (meta.triage_result) items.push({ label: "Triage", value: String(meta.triage_result) });
  if (meta.triage_backend)
    items.push({ label: "Triage Backend", value: `${(meta.triage_backend as string[]).join(", ")}${meta.triage_model ? ` (${meta.triage_model})` : ""}` });
  if (meta.review_type === "lightweight" && meta.lightweight_backend)
    items.push({ label: "Reviewer", value: `${(meta.lightweight_backend as string[]).join(", ")}${meta.lightweight_model ? ` (${meta.lightweight_model})` : ""}` });
  if (meta.reconciler_backend)
    items.push({ label: "Reconciler", value: `${(meta.reconciler_backend as string[]).join(", ")}${meta.reconciler_model ? ` (${meta.reconciler_model})` : ""}` });
  if (meta.base_ref) items.push({ label: "Base", value: String(meta.base_ref) });
  if (meta.head_sha) items.push({ label: "SHA", value: String(meta.head_sha).slice(0, 12) });
  if (meta.additions != null || meta.deletions != null)
    items.push({ label: "Size", value: `+${meta.additions ?? 0} / -${meta.deletions ?? 0}` });
  if (meta.total_duration_seconds != null) items.push({ label: "Duration", value: `${meta.total_duration_seconds}s` });
  if (trigger) {
    const parts = [trigger.type || "unknown"];
    if (trigger.by) parts.push(`by ${trigger.by}`);
    if (trigger.force) parts.push("(forced)");
    items.push({ label: "Trigger", value: parts.join(" ") });
  }
  if (meta.review_mode) items.push({ label: "Mode", value: String(meta.review_mode) });

  if (items.length === 0 && !reviewers) return null;

  return (
    <div className="mb-6">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-300 transition-colors"
      >
        <svg
          className={`w-3 h-3 transition-transform duration-200 ${open ? "rotate-90" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M8.25 4.5l7.5 7.5-7.5 7.5" />
        </svg>
        Review Parameters
      </button>
      {open && (
        <div className="mt-2 px-4 py-3 rounded-lg bg-surface-3/40 border border-surface-border animate-fade-in">
          <div className="flex flex-wrap gap-x-6 gap-y-1.5">
            {items.map((item) => (
              <MetaItem key={item.label} label={item.label} value={item.value} />
            ))}
          </div>
          {reviewers && (
            <div className="mt-3 pt-2.5 border-t border-surface-border/50">
              <div className="grid gap-2">
                {Object.entries(reviewers).map(([name, info]) => (
                  <div key={name} className="flex items-center gap-3 text-xs">
                    <span className={`font-medium ${getTheme(name).activeText}`}>
                      {getTheme(name).label}
                    </span>
                    {info.model && <span className="text-gray-400 font-mono">{info.model}</span>}
                    {info.backend && <span className="text-gray-600">{info.backend}</span>}
                    {info.status && (
                      <span className={info.status === "ok" ? "text-emerald-500" : "text-rose-400"}>
                        {info.status}
                      </span>
                    )}
                    {info.duration_seconds != null && (
                      <span className="text-gray-600">{info.duration_seconds}s</span>
                    )}
                    {info.tokens && (
                      <span className="text-gray-600 font-mono">
                        {info.tokens.input.toLocaleString()}→{info.tokens.output.toLocaleString()} tok
                        {info.tokens.cost_usd != null && (
                          <> · ${info.tokens.cost_usd}</>
                        )}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {totalTokens && (
            <div className="mt-2.5 pt-2 border-t border-surface-border/50 flex items-center gap-3 text-xs">
              <span className="text-gray-500">Total</span>
              <span className="text-gray-400 font-mono">
                {totalTokens.input.toLocaleString()}→{totalTokens.output.toLocaleString()} tok
                {totalTokens.cost_usd != null && <> · ${totalTokens.cost_usd}</>}
              </span>
            </div>
          )}
          {changedFiles && changedFiles.length > 0 && (
            <details className="mt-2.5 pt-2 border-t border-surface-border/50">
              <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-300 transition-colors">
                {changedFiles.length} file{changedFiles.length !== 1 ? "s" : ""} changed
              </summary>
              <div className="mt-1.5 text-xs font-mono text-gray-500 space-y-0.5 max-h-40 overflow-y-auto">
                {changedFiles.map((f) => <div key={f}>{f}</div>)}
              </div>
            </details>
          )}
          {!!meta.custom_prompt_paths && (
            <div className="mt-2.5 pt-2 border-t border-surface-border/50 flex flex-wrap gap-x-4 gap-y-1 text-xs">
              <span className="text-gray-500">Custom prompts:</span>
              {Object.entries(meta.custom_prompt_paths as Record<string, string>).map(([stage, path]) => (
                <span key={stage} className="text-gray-500">
                  <span className="text-gray-400">{stage}</span> → <span className="font-mono text-gray-600">{path}</span>
                </span>
              ))}
            </div>
          )}
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

  // Find conversation steps for the active tab
  const stageConversations = data.stage_conversations;
  const conversationEvents =
    activeTab !== "final" && stageConversations
      ? stageConversations[activeTab] || null
      : null;

  return (
    <div className="animate-fade-in-up">
      <div className="flex items-center gap-3 mb-1">
        <h1 className="text-2xl font-bold text-gray-100 tracking-tight">
          <span className="text-gray-500 font-mono">#</span>
          {number}
        </h1>
        <DecisionBadge decision={data.decision} />
        <TypeBadge type={data.review_type} />
      </div>

      {(data.title || data.author) && (
        <p className="text-sm text-gray-400 mb-1">
          {data.title && (
            <span className="text-gray-300">{data.title}</span>
          )}
          {data.title && data.author && (
            <span className="text-gray-600 mx-1.5">by</span>
          )}
          {data.author && (
            <span className="font-mono text-gray-500">{data.author}</span>
          )}
        </p>
      )}

      {isHistorical && version && (
        <p className="text-sm font-mono text-gray-500 mb-4">{version}</p>
      )}

      {data.meta && <ReviewMetaPanel meta={data.meta} />}

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
          {conversationEvents && <ConversationSteps events={conversationEvents} />}
          <MarkdownView content={activeContent} />
        </div>
      </div>
    </div>
  );
}
