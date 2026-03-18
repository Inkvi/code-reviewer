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

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (!data) return <p className="text-gray-500">Not found.</p>;

  const stageContents = data.stage_contents;
  const tabs = [
    { id: "final", label: "Final Review" },
    ...data.stages.map((s) => ({ id: s, label: stageLabel(s) })),
  ];

  const activeContent =
    activeTab === "final"
      ? data.final_review
      : stageContents[activeTab] || "No content available.";

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-bold">
          {isHistorical ? `PR #${number} - ${version}` : `PR #${number}`}
        </h1>
        <DecisionBadge decision={data.decision} />
        <TypeBadge type={data.review_type} />
      </div>

      {!isHistorical && "versions" in data && data.versions.length > 0 && (
        <div className="mb-4">
          <Link
            to={`/${org}/${repo}/${number}/history`}
            className="text-sm text-blue-600 hover:text-blue-800"
          >
            View {data.versions.length} historical version
            {data.versions.length !== 1 ? "s" : ""}
          </Link>
        </div>
      )}

      <div className="bg-white rounded-lg border border-gray-200">
        <div className="border-b border-gray-200 px-4">
          <div className="flex gap-1 -mb-px overflow-x-auto">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-3 py-2.5 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
                  activeTab === tab.id
                    ? "border-blue-500 text-blue-600"
                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                }`}
              >
                <span className="flex items-center gap-1.5">
                  {tab.label}
                  {tab.id !== "final" && <StageBadge stage={tab.id} />}
                </span>
              </button>
            ))}
          </div>
        </div>
        <div className="p-6">
          <MarkdownView content={activeContent} />
        </div>
      </div>
    </div>
  );
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    lightweight: "Lightweight",
    claude: "Claude",
    codex: "Codex",
    gemini: "Gemini",
    reconcile: "Reconciled",
  };
  return labels[stage] || stage;
}
