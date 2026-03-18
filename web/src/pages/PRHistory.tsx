import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchPRHistory, type VersionSummary } from "../api";
import { StageBadge } from "../components/Badge";

function formatTimestamp(ts: string): string {
  const match = ts.match(
    /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/
  );
  if (!match) return ts;
  const [, y, mo, d, h, mi, s] = match;
  return `${y}-${mo}-${d} ${h}:${mi}:${s} UTC`;
}

export default function PRHistory() {
  const { org, repo, number } = useParams<{
    org: string;
    repo: string;
    number: string;
  }>();
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!org || !repo || !number) return;
    fetchPRHistory(org, repo, parseInt(number, 10))
      .then(setVersions)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [org, repo, number]);

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
  if (versions.length === 0)
    return <p className="text-gray-500">No version history for this PR.</p>;

  return (
    <div className="animate-fade-in-up">
      <h1 className="text-2xl font-bold text-gray-100 mb-8 tracking-tight">
        <span className="text-gray-500 font-mono">#</span>
        {number}
        <span className="text-gray-500 font-normal ml-3 text-lg">
          Version History
        </span>
      </h1>
      <div className="space-y-3">
        {versions.map((v, i) => (
          <Link
            key={v.version}
            to={`/${org}/${repo}/${number}/history/${v.version}`}
            className="group block bg-surface-2 rounded-xl border border-surface-border p-5 hover:border-surface-border-hover hover:bg-surface-3 transition-all duration-200"
            style={{ animationDelay: `${i * 50}ms` }}
          >
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium text-gray-200 group-hover:text-gray-100 transition-colors">
                  {formatTimestamp(v.timestamp)}
                </div>
                <div className="text-sm text-gray-600 mt-1.5 font-mono tracking-wide">
                  {v.sha}
                </div>
              </div>
              <div className="flex gap-1.5">
                {v.stages.map((s) => (
                  <StageBadge key={s} stage={s} />
                ))}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
