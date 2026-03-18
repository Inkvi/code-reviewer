import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  fetchPRDetail,
  fetchVersionDetail,
  type PRDetailData,
  type VersionDetailData,
} from "../api";
import { DecisionBadge, TypeBadge, StageBadge } from "../components/Badge";
import MarkdownView from "../components/MarkdownView";

interface Props {
  isHistorical?: boolean;
}

type DetailData = PRDetailData | VersionDetailData;

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
  const reviewStages = data.stages.filter((s) => !isPromptStage(s));
  const promptStages = data.stages.filter((s) => isPromptStage(s));
  const tabs = [
    { id: "final", label: "Final Review" },
    ...reviewStages.map((s) => ({ id: s, label: stageLabel(s) })),
  ];
  const promptTabs = promptStages.map((s) => ({
    id: s,
    label: stageLabel(s),
  }));

  const activeContent =
    activeTab === "final"
      ? data.final_review
      : stageContents[activeTab] || "No content available.";

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
          <div className="flex gap-1 overflow-x-auto">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`relative px-3.5 py-3 text-sm font-medium whitespace-nowrap transition-colors duration-150 ${
                  activeTab === tab.id
                    ? "text-accent-blue"
                    : "text-gray-500 hover:text-gray-300"
                }`}
              >
                <span className="flex items-center gap-2">
                  {tab.label}
                  {tab.id !== "final" && <StageBadge stage={tab.id} />}
                </span>
                {activeTab === tab.id && (
                  <span className="absolute bottom-0 left-1.5 right-1.5 h-0.5 rounded-full bg-accent-blue" />
                )}
              </button>
            ))}
            {promptTabs.length > 0 && (
              <>
                <div className="self-center mx-1 w-px h-5 bg-surface-border" />
                {promptTabs.map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={`relative px-3.5 py-3 text-sm font-medium whitespace-nowrap transition-colors duration-150 ${
                      activeTab === tab.id
                        ? "text-amber-400"
                        : "text-gray-600 hover:text-gray-400"
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      {tab.label}
                    </span>
                    {activeTab === tab.id && (
                      <span className="absolute bottom-0 left-1.5 right-1.5 h-0.5 rounded-full bg-amber-400" />
                    )}
                  </button>
                ))}
              </>
            )}
          </div>
        </div>
        {/* Content */}
        <div className="p-6 animate-fade-in" key={activeTab}>
          <MarkdownView content={activeContent} />
        </div>
      </div>
    </div>
  );
}

function isPromptStage(stage: string): boolean {
  return stage.endsWith(".prompt");
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    lightweight: "Lightweight",
    claude: "Claude",
    codex: "Codex",
    gemini: "Gemini",
    reconcile: "Reconciled",
    "triage.prompt": "Triage Prompt",
    "lightweight.prompt": "Lightweight Prompt",
    "claude.prompt": "Claude Prompt",
    "codex.prompt": "Codex Prompt",
    "gemini.prompt": "Gemini Prompt",
    "reconcile.prompt": "Reconcile Prompt",
  };
  return labels[stage] || stage;
}
