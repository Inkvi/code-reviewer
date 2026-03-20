const BASE = "/api";

export interface Repo {
  org: string;
  repo: string;
  pr_count: number;
}

export interface PRSummary {
  number: number;
  review_type: string;
  decision: string | null;
  stages: string[];
  version_count: number;
  author: string | null;
  title: string | null;
}

export type ReviewMeta = Record<string, unknown>;

export type ConversationEvent = Record<string, unknown>;

export interface PRDetailData {
  number: number;
  org: string;
  repo: string;
  review_type: string;
  decision: string;
  final_review: string;
  stages: string[];
  stage_contents: Record<string, string>;
  stage_conversations?: Record<string, ConversationEvent[]> | null;
  versions: VersionSummary[];
  author: string | null;
  title: string | null;
  meta: ReviewMeta | null;
}

export interface VersionSummary {
  version: string;
  timestamp: string;
  sha: string;
  stages: string[];
  has_final: boolean;
}

export interface VersionDetailData {
  version: string;
  timestamp: string;
  sha: string;
  final_review: string;
  stages: string[];
  stage_contents: Record<string, string>;
  stage_conversations?: Record<string, ConversationEvent[]> | null;
  decision: string;
  review_type: string;
  author: string | null;
  title: string | null;
  meta: ReviewMeta | null;
}

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export function fetchRepos(): Promise<Repo[]> {
  return fetchJSON("/repos");
}

export function fetchPRs(org: string, repo: string): Promise<PRSummary[]> {
  return fetchJSON(`/repos/${org}/${repo}/prs`);
}

export function fetchPRDetail(
  org: string,
  repo: string,
  number: number
): Promise<PRDetailData> {
  return fetchJSON(`/repos/${org}/${repo}/prs/${number}`);
}

export function fetchPRHistory(
  org: string,
  repo: string,
  number: number
): Promise<VersionSummary[]> {
  return fetchJSON(`/repos/${org}/${repo}/prs/${number}/history`);
}

export function fetchVersionDetail(
  org: string,
  repo: string,
  number: number,
  version: string
): Promise<VersionDetailData> {
  return fetchJSON(`/repos/${org}/${repo}/prs/${number}/history/${version}`);
}
