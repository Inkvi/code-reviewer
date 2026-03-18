import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchPRs, type PRSummary } from "../api";
import { DecisionBadge, TypeBadge, StageBadge } from "../components/Badge";

export default function PRList() {
  const { org, repo } = useParams<{ org: string; repo: string }>();
  const [prs, setPrs] = useState<PRSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!org || !repo) return;
    fetchPRs(org, repo)
      .then(setPrs)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [org, repo]);

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (prs.length === 0)
    return <p className="text-gray-500">No PRs reviewed for this repo.</p>;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">
        {org}/{repo}
      </h1>
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                PR
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                Decision
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                Type
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                Stages
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                Versions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {prs.map((pr) => (
              <tr key={pr.number} className="hover:bg-gray-50">
                <td className="px-4 py-3">
                  <Link
                    to={`/${org}/${repo}/${pr.number}`}
                    className="text-blue-600 hover:text-blue-800 font-medium"
                  >
                    #{pr.number}
                  </Link>
                </td>
                <td className="px-4 py-3">
                  <DecisionBadge decision={pr.decision} />
                </td>
                <td className="px-4 py-3">
                  <TypeBadge type={pr.review_type} />
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-1 flex-wrap">
                    {pr.stages.map((s) => (
                      <StageBadge key={s} stage={s} />
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">
                  {pr.version_count > 0 && (
                    <Link
                      to={`/${org}/${repo}/${pr.number}/history`}
                      className="text-blue-600 hover:text-blue-800"
                    >
                      {pr.version_count} version
                      {pr.version_count !== 1 ? "s" : ""}
                    </Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
