import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchPRHistory, type VersionSummary } from "../api";
import { StageBadge } from "../components/Badge";

function formatTimestamp(ts: string): string {
  // Format: 20260318T120000Z -> 2026-03-18 12:00:00 UTC
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

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (versions.length === 0)
    return <p className="text-gray-500">No version history for this PR.</p>;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">
        PR #{number} - Version History
      </h1>
      <div className="space-y-3">
        {versions.map((v) => (
          <Link
            key={v.version}
            to={`/${org}/${repo}/${number}/history/${v.version}`}
            className="block bg-white rounded-lg border border-gray-200 p-4 hover:border-blue-400 hover:shadow-sm transition-all"
          >
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium text-gray-900">
                  {formatTimestamp(v.timestamp)}
                </div>
                <div className="text-sm text-gray-500 mt-1 font-mono">
                  {v.sha}
                </div>
              </div>
              <div className="flex gap-1">
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
