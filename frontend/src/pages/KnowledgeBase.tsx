/**
 * Knowledge Base - Real API integration
 * Fetches documents from /api/knowledge/documents
 */

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Book,
  Search,
  Plus,
  FileText,
  Tag,
  Clock,
  User,
  ChevronRight,
  Edit3,
  BookOpen,
  Code,
  AlertCircle,
  CheckCircle,
  Link as LinkIcon,
  FolderOpen,
  Star,
  StarOff,
  RefreshCw,
  Loader2,
  Trash2,
} from "lucide-react";

import { apiFetch, describeFailure } from "../lib/apiClient";

interface KnowledgeArticle {
  id: string;
  title: string;
  content: string;
  doc_type: "runbook" | "postmortem" | "documentation" | "faq" | "alert_pattern" | "incident_resolution" | "architecture";
  tags: string[];
  author: string;
  service: string | null;
  created_at: string;
  updated_at?: string;
  starred?: boolean;
}

const CATEGORIES = [
  { id: "all", label: "All", icon: <FolderOpen className="w-4 h-4" /> },
  { id: "runbook", label: "Runbooks", icon: <FileText className="w-4 h-4" /> },
  { id: "documentation", label: "Documentation", icon: <BookOpen className="w-4 h-4" /> },
  { id: "faq", label: "FAQ", icon: <CheckCircle className="w-4 h-4" /> },
  { id: "alert_pattern", label: "Alert Patterns", icon: <AlertCircle className="w-4 h-4" /> },
  { id: "architecture", label: "Architecture", icon: <Code className="w-4 h-4" /> },
  { id: "postmortem", label: "Postmortems", icon: <FileText className="w-4 h-4" /> },
];

function ArticleCard({
  article,
  onSelect,
  onToggleStar,
  onDelete,
}: {
  article: KnowledgeArticle;
  onSelect: () => void;
  onToggleStar: () => void;
  onDelete: () => void;
}) {
  const categoryIcons: Record<string, React.ReactNode> = {
    runbook: <FileText className="w-4 h-4" />,
    documentation: <BookOpen className="w-4 h-4" />,
    faq: <CheckCircle className="w-4 h-4" />,
    alert_pattern: <AlertCircle className="w-4 h-4" />,
    architecture: <Code className="w-4 h-4" />,
    postmortem: <FileText className="w-4 h-4" />,
    incident_resolution: <CheckCircle className="w-4 h-4" />,
  };

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString();
    } catch {
      return dateStr;
    }
  };

  return (
    <motion.div
      className="rounded-xl border p-4 cursor-pointer hover:border-blue-500/50 transition-all group"
      style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
      whileHover={{ scale: 1.01 }}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ backgroundColor: "var(--panel)" }}
          >
            {categoryIcons[article.doc_type] || <FileText className="w-4 h-4" />}
          </div>
          <span className="text-xs px-2 py-0.5 rounded-full capitalize" style={{ backgroundColor: "var(--panel)" }}>
            {article.doc_type.replace(/_/g, " ")}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {/* Both of these are icon-only, so without a label a screen reader
              announces "button" twice on every card in the grid. */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleStar();
            }}
            className="p-1 hover:bg-white/10 rounded"
            title={article.starred ? "Unstar" : "Star"}
            aria-label={`${article.starred ? "Unstar" : "Star"} ${article.title}`}
          >
            {article.starred ? (
              <Star className="w-4 h-4 text-yellow-500 fill-yellow-500" />
            ) : (
              <StarOff className="w-4 h-4 text-gray-500" />
            )}
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            className="p-1 hover:bg-red-500/20 rounded text-gray-500 hover:text-red-400"
            title="Delete"
            aria-label={`Delete ${article.title}`}
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      <h3 className="font-semibold mb-2 group-hover:text-blue-400 transition-colors">
        {article.title}
      </h3>

      <p className="text-sm text-gray-400 line-clamp-2 mb-3">
        {article.content.split("\n")[0].replace(/^#+\s*/, "")}
      </p>

      <div className="flex flex-wrap gap-1 mb-3">
        {article.tags.slice(0, 3).map((tag) => (
          <span
            key={tag}
            className="text-xs px-2 py-0.5 rounded"
            style={{ backgroundColor: "var(--panel)" }}
          >
            #{tag}
          </span>
        ))}
        {article.tags.length > 3 && (
          <span className="text-xs text-gray-500">+{article.tags.length - 3}</span>
        )}
      </div>

      <div className="flex items-center justify-between text-xs text-gray-500">
        <div className="flex items-center gap-1">
          <User className="w-3 h-3" />
          {article.author || "Unknown"}
        </div>
        <div className="flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {formatDate(article.created_at)}
        </div>
      </div>
    </motion.div>
  );
}

/** The article viewer, with the editor the pencil used to only pretend to open.
 *
 *  `onEdit` was `() => setSelectedArticle(null)`: clicking the pencil closed the
 *  modal and did nothing else, so an operator who spotted a wrong command in a
 *  runbook had no way to correct it short of deleting the document and writing
 *  it again. PUT /api/knowledge/documents/{doc_id} had been there all along. */
function ArticleViewer({
  article,
  onClose,
  onSave,
  saving,
}: {
  article: KnowledgeArticle;
  onClose: () => void;
  onSave: (patch: { title: string; content: string; tags: string[]; service: string | null }) => Promise<void>;
  saving: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(article.title);
  const [content, setContent] = useState(article.content);
  const [tags, setTags] = useState(article.tags.join(", "));
  const [service, setService] = useState(article.service ?? "");
  const [editError, setEditError] = useState<string | null>(null);

  const startEditing = () => {
    // Always from what is currently stored, so re-opening the editor after a
    // failed save does not resurrect a stale draft.
    setTitle(article.title);
    setContent(article.content);
    setTags(article.tags.join(", "));
    setService(article.service ?? "");
    setEditError(null);
    setEditing(true);
  };

  const save = async () => {
    if (!title.trim() || !content.trim()) {
      setEditError("An article needs a title and content.");
      return;
    }
    setEditError(null);
    try {
      await onSave({
        title: title.trim(),
        content: content.trim(),
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        service: service.trim() || null,
      });
      setEditing(false);
    } catch (err) {
      // Stay in the editor with the text still in it — the operator's rewrite is
      // the only copy, and closing over it would lose the work.
      setEditError(err instanceof Error ? err.message : "Could not save this article");
    }
  };

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString();
    } catch {
      return dateStr;
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="w-full max-w-3xl max-h-[80vh] overflow-hidden rounded-2xl flex flex-col"
        style={{ backgroundColor: "var(--panel)", border: "1px solid var(--border)" }}
      >
        <div className="flex items-center justify-between p-6 border-b" style={{ borderColor: "var(--border)" }}>
          <div className="flex-1 min-w-0 mr-4">
            {editing ? (
              <input
                aria-label="Title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full px-3 py-2 text-xl font-semibold rounded-lg border"
                style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
              />
            ) : (
              <h2 className="text-xl font-semibold">{article.title}</h2>
            )}
            <div className="flex items-center gap-4 mt-2 text-sm text-gray-400">
              <span className="flex items-center gap-1">
                <User className="w-4 h-4" />
                {article.author || "Unknown"}
              </span>
              <span className="flex items-center gap-1">
                <Clock className="w-4 h-4" />
                {formatDate(article.created_at)}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {editing ? (
              <>
                <button
                  onClick={save}
                  disabled={saving || !title.trim() || !content.trim()}
                  className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
                >
                  {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                  {saving ? "Saving..." : "Save"}
                </button>
                <button
                  onClick={() => { setEditing(false); setEditError(null); }}
                  className="px-4 py-2 rounded-lg hover:bg-white/10"
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={startEditing}
                  className="p-2 rounded-lg hover:bg-white/10"
                  title="Edit"
                  aria-label="Edit article"
                >
                  <Edit3 className="w-5 h-5" />
                </button>
                <button
                  onClick={onClose}
                  className="p-2 rounded-lg hover:bg-white/10"
                  aria-label="Close"
                >
                  <span className="text-xl">&times;</span>
                </button>
              </>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {editing ? (
            <div className="space-y-4">
              {editError && (
                <div role="alert" className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg p-3">
                  {editError}
                </div>
              )}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-2">Tags (comma-separated)</label>
                  <input
                    aria-label="Tags"
                    value={tags}
                    onChange={(e) => setTags(e.target.value)}
                    className="w-full px-4 py-2 rounded-lg border"
                    style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Service</label>
                  <input
                    aria-label="Service"
                    value={service}
                    onChange={(e) => setService(e.target.value)}
                    className="w-full px-4 py-2 rounded-lg border"
                    style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium mb-2">Content (Markdown)</label>
                <textarea
                  aria-label="Content"
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  rows={16}
                  className="w-full px-4 py-2 rounded-lg border font-mono text-sm"
                  style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                />
              </div>
              {/* Not offered: the update endpoint takes title, content, tags and
                  service, and nothing else. A document type control here would
                  be a field the server discards. */}
              <p className="text-xs text-gray-500">
                Document type ({article.doc_type.replace("_", " ")}) cannot be changed after creation.
              </p>
            </div>
          ) : (
          <>
          <div className="flex flex-wrap gap-2 mb-6">
            {article.tags.map((tag) => (
              <span
                key={tag}
                className="text-sm px-3 py-1 rounded-full"
                style={{ backgroundColor: "var(--shell)" }}
              >
                <Tag className="w-3 h-3 inline mr-1" />
                {tag}
              </span>
            ))}
          </div>

          {article.service && (
            <div className="mb-6 p-3 rounded-lg" style={{ backgroundColor: "var(--shell)" }}>
              <div className="text-sm font-medium mb-2 flex items-center gap-2">
                <LinkIcon className="w-4 h-4" />
                Linked Service
              </div>
              <span className="text-sm px-2 py-1 rounded bg-blue-500/20 text-blue-400">
                {article.service}
              </span>
            </div>
          )}

          <div className="prose prose-invert max-w-none">
            {article.content.split("\n").map((line, i) => {
              if (line.startsWith("## ")) {
                return <h2 key={i} className="text-lg font-semibold mt-6 mb-3">{line.replace("## ", "")}</h2>;
              }
              if (line.startsWith("- ")) {
                return <li key={i} className="text-gray-300 ml-4">{line.replace("- ", "")}</li>;
              }
              if (line.match(/^\d+\. /)) {
                return <li key={i} className="text-gray-300 ml-4 list-decimal">{line.replace(/^\d+\. /, "")}</li>;
              }
              if (line.startsWith("```")) {
                return null;
              }
              if (line.includes("`")) {
                const parts = line.split(/`([^`]+)`/);
                return (
                  <p key={i} className="text-gray-300 my-2">
                    {parts.map((part, j) =>
                      j % 2 === 1 ? (
                        <code key={j} className="px-1.5 py-0.5 rounded bg-black/30 text-sm font-mono">
                          {part}
                        </code>
                      ) : (
                        part
                      )
                    )}
                  </p>
                );
              }
              if (line.startsWith("**") && line.endsWith("**")) {
                return <p key={i} className="font-semibold text-white my-2">{line.replace(/\*\*/g, "")}</p>;
              }
              if (line.trim()) {
                return <p key={i} className="text-gray-300 my-2">{line}</p>;
              }
              return null;
            })}
          </div>
          </>
          )}
        </div>
      </motion.div>
    </div>
  );
}

function CreateArticleModal({
  onClose,
  onSave,
  saving,
}: {
  onClose: () => void;
  onSave: (article: { title: string; content: string; doc_type: string; tags: string[]; service?: string; author?: string }) => Promise<void>;
  saving: boolean;
}) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [docType, setDocType] = useState("runbook");
  const [tags, setTags] = useState("");
  const [service, setService] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    if (!title.trim() || !content.trim()) {
      setError("Title and content are required");
      return;
    }

    setError(null);
    try {
      await onSave({
        title: title.trim(),
        content: content.trim(),
        doc_type: docType,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        service: service.trim() || undefined,
        author: "You",
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create article");
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="w-full max-w-2xl rounded-2xl"
        style={{ backgroundColor: "var(--panel)", border: "1px solid var(--border)" }}
      >
        <div className="flex items-center justify-between p-6 border-b" style={{ borderColor: "var(--border)" }}>
          <h2 className="text-xl font-semibold">Create Knowledge Article</h2>
          <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-lg">
            <span className="text-xl">&times;</span>
          </button>
        </div>

        <div className="p-6 space-y-4 max-h-[60vh] overflow-y-auto">
          <div>
            <label className="block text-sm font-medium mb-2">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border"
              style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
              placeholder="e.g., How to debug memory leaks"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-2">Document Type</label>
              <select
                value={docType}
                onChange={(e) => setDocType(e.target.value)}
                className="w-full px-4 py-2 rounded-lg border"
                style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
              >
                <option value="runbook">Runbook</option>
                <option value="documentation">Documentation</option>
                <option value="faq">FAQ</option>
                <option value="alert_pattern">Alert Pattern</option>
                <option value="architecture">Architecture</option>
                <option value="postmortem">Postmortem</option>
                <option value="incident_resolution">Incident Resolution</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium mb-2">Service (optional)</label>
              <input
                type="text"
                value={service}
                onChange={(e) => setService(e.target.value)}
                className="w-full px-4 py-2 rounded-lg border"
                style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                placeholder="e.g., payment-service"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Tags (comma-separated)</label>
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border"
              style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
              placeholder="e.g., database, performance, debugging"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Content (Markdown)</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={10}
              className="w-full px-4 py-2 rounded-lg border font-mono text-sm"
              style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
              placeholder="## Overview&#10;&#10;Describe the topic...&#10;&#10;## Steps&#10;&#10;1. First step&#10;2. Second step"
            />
          </div>

          {error && (
            <div className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg p-3">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 p-6 border-t" style={{ borderColor: "var(--border)" }}>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg hover:bg-white/10"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !title.trim() || !content.trim()}
            className="px-6 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
          >
            {saving && <Loader2 className="w-4 h-4 animate-spin" />}
            {saving ? "Creating..." : "Create Article"}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

/** What the API will return in one page. Its own default is 50 and its ceiling
 *  is 100; the page used to send neither and then report the truncated result
 *  as the whole knowledge base. */
const PAGE_SIZE = 50;

export function KnowledgeBase({ onBack }: { onBack: () => void }) {
  const [articles, setArticles] = useState<KnowledgeArticle[]>([]);
  // How many documents match, which is not how many are on screen. The header
  // reported `articles.length` — so a base of 55 documents said "50 articles"
  // and the other five were unreachable, with no control to page to them.
  const [total, setTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [selectedArticle, setSelectedArticle] = useState<KnowledgeArticle | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showStarredOnly, setShowStarredOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [starredIds, setStarredIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  // Fetch articles from API
  const fetchArticles = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (category !== "all") params.set("doc_type", category);
      if (search) params.set("search", search);
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(offset));

      const res = await apiFetch(`/api/knowledge/documents?${params}`, {
        credentials: "include",
      });

      // `if (res.ok) { ... }` with no else. A 401, a 403 from the feature gate
      // or a 500 left `articles` at whatever it already was — [] on first load —
      // and the page rendered its "No articles found" empty state. An operator
      // searching the knowledge base during an incident was told the runbook
      // does not exist when the truth was that their session had expired.
      if (!res.ok) throw new Error(await describeFailure(res));

      const data = await res.json();
      const documents: KnowledgeArticle[] = data.documents || [];
      setArticles(
        documents.map((doc: KnowledgeArticle) => ({
          ...doc,
          starred: starredIds.has(doc.id),
        }))
      );
      // The server's count of everything that matches. Falls back to what came
      // back rather than to 0: an unknown total is not an empty knowledge base.
      setTotal(typeof data.total === "number" ? data.total : documents.length);
      setError(null);
    } catch (err) {
      console.error("Failed to fetch articles:", err);
      setArticles([]);
      setTotal(null);
      setError(err instanceof Error ? err.message : "Failed to load the knowledge base");
    } finally {
      setLoading(false);
    }
  }, [category, search, starredIds, offset]);

  useEffect(() => {
    fetchArticles();
  }, [fetchArticles]);

  // A new search or category is a new result set, so page 2 of the old one is
  // meaningless — and landing on an empty page would read as "no matches".
  useEffect(() => {
    setOffset(0);
  }, [category, search]);

  // Load starred IDs from localStorage
  useEffect(() => {
    const stored = localStorage.getItem("kb_starred");
    if (stored) {
      try {
        setStarredIds(new Set(JSON.parse(stored)));
      } catch {
        // ignore
      }
    }
  }, []);

  const toggleStar = (id: string) => {
    setStarredIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      localStorage.setItem("kb_starred", JSON.stringify([...next]));
      return next;
    });
    setArticles((prev) =>
      prev.map((a) => (a.id === id ? { ...a, starred: !a.starred } : a))
    );
  };

  const handleCreateArticle = async (newArticle: {
    title: string;
    content: string;
    doc_type: string;
    tags: string[];
    service?: string;
    author?: string;
  }) => {
    setSaving(true);
    try {
      const res = await apiFetch(`/api/knowledge/documents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(newArticle),
      });

      if (!res.ok) throw new Error(await describeFailure(res));

      setError(null);
      await fetchArticles();
    } finally {
      setSaving(false);
    }
  };

  /** Save an operator's correction to an existing article. */
  const handleUpdateArticle = async (
    id: string,
    patch: { title: string; content: string; tags: string[]; service: string | null }
  ) => {
    setSaving(true);
    try {
      const res = await apiFetch(`/api/knowledge/documents/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error(await describeFailure(res));

      // PUT answers `{"message": "Document updated"}` and nothing else, so read
      // the document back: what goes on screen is then what the server kept,
      // not what this page hoped it kept.
      const reread = await apiFetch(`/api/knowledge/documents/${id}`, {
        credentials: "include",
      });
      if (!reread.ok) throw new Error(await describeFailure(reread));
      const stored: KnowledgeArticle = await reread.json();
      const fresh = { ...stored, starred: starredIds.has(stored.id) };

      setArticles((prev) => prev.map((a) => (a.id === id ? fresh : a)));
      setSelectedArticle((prev) => (prev?.id === id ? fresh : prev));
      setError(null);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteArticle = async (id: string) => {
    if (!confirm("Are you sure you want to delete this article?")) return;

    try {
      const res = await apiFetch(`/api/knowledge/documents/${id}`, {
        method: "DELETE",
        credentials: "include",
      });

      // The old code removed the row from local state only `if (res.ok)`, and
      // said nothing at all otherwise — so a failed delete left the article on
      // screen and looked to the operator like a mis-click. Worse in the other
      // direction: nothing here told them the document is still in the index and
      // will come back on the next search.
      if (!res.ok) throw new Error(await describeFailure(res));

      setArticles((prev) => prev.filter((a) => a.id !== id));
      setSelectedArticle((prev) => (prev?.id === id ? null : prev));
      setError(null);
    } catch (err) {
      console.error("Failed to delete article:", err);
      setError(`Could not delete this article: ${err instanceof Error ? err.message : "request failed"}`);
    }
  };

  const filteredArticles = articles.filter((article) => {
    const matchesStarred = !showStarredOnly || article.starred;
    return matchesStarred;
  });

  return (
    <div className="min-h-screen omni-workspace-page" style={{ backgroundColor: "var(--bg)" }}>
      <header className="border-b" style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <button onClick={onBack} className="text-gray-400 hover:text-white">
                <ChevronRight className="w-5 h-5 rotate-180" />
              </button>
              <div className="flex items-center gap-2">
                <Book className="w-6 h-6 text-blue-400" />
                <div>
                  <h1 className="text-xl font-semibold">Knowledge Base</h1>
                  <p className="text-sm text-gray-400">
                    {loading
                      ? "Loading..."
                      : total === null
                        ? "article count unavailable"
                        : `${total} article${total === 1 ? "" : "s"}`}
                  </p>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={fetchArticles}
                className="p-2 hover:bg-white/10 rounded-lg"
                title="Refresh"
              >
                <RefreshCw className={`w-5 h-5 ${loading ? "animate-spin" : ""}`} />
              </button>
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search articles..."
                  className="pl-10 pr-4 py-2 rounded-lg border w-64"
                  style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                />
              </div>
              <button
                onClick={() => setShowStarredOnly(!showStarredOnly)}
                className={`p-2 rounded-lg transition-colors ${
                  showStarredOnly ? "bg-yellow-500/20 text-yellow-500" : "hover:bg-white/10"
                }`}
                title="Show starred only"
              >
                <Star className="w-5 h-5" />
              </button>
              <button
                onClick={() => setShowCreateModal(true)}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700"
              >
                <Plus className="w-4 h-4" />
                New Article
              </button>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Categories */}
        <div className="flex items-center gap-2 mb-6 overflow-x-auto pb-2">
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setCategory(cat.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg whitespace-nowrap transition-colors ${
                category === cat.id
                  ? "bg-blue-600 text-white"
                  : "hover:bg-white/10"
              }`}
              style={category !== cat.id ? { backgroundColor: "var(--shell)" } : {}}
            >
              {cat.icon}
              {cat.label}
            </button>
          ))}
        </div>

        {error && (
          <div
            role="alert"
            className="mb-6 rounded-xl px-4 py-3 flex items-start justify-between gap-4"
            style={{ border: "1px solid var(--bad)", backgroundColor: "rgba(239,68,68,0.08)" }}
          >
            <span className="text-sm" style={{ color: "var(--bad)" }}>{error}</span>
            <button
              onClick={() => { setError(null); fetchArticles(); }}
              className="text-sm underline shrink-0"
              style={{ color: "var(--bad)" }}
            >
              Retry
            </button>
          </div>
        )}

        {/* Loading State */}
        {loading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
          </div>
        )}

        {/* Articles Grid */}
        {!loading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredArticles.map((article) => (
              <ArticleCard
                key={article.id}
                article={article}
                onSelect={() => setSelectedArticle(article)}
                onToggleStar={() => toggleStar(article.id)}
                onDelete={() => handleDeleteArticle(article.id)}
              />
            ))}
          </div>
        )}

        {/* Paging, for the documents past the first 50. Without this the API's
            page cap silently truncated every list, search and category filter —
            an operator could be told a runbook does not exist when it was only
            on page 2. */}
        {!loading && !error && total !== null && total > PAGE_SIZE && (
          <div className="flex items-center justify-between mt-6 text-sm">
            <span className="text-gray-400">
              Showing {offset + 1}–{Math.min(offset + articles.length, total)} of {total}
              {showStarredOnly && " · the star filter applies to this page only"}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0}
                className="px-3 py-1.5 rounded-lg hover:bg-white/10 disabled:opacity-40"
                style={{ backgroundColor: "var(--shell)" }}
              >
                Previous
              </button>
              <button
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={offset + articles.length >= total}
                className="px-3 py-1.5 rounded-lg hover:bg-white/10 disabled:opacity-40"
                style={{ backgroundColor: "var(--shell)" }}
              >
                Next
              </button>
            </div>
          </div>
        )}

        {/* Suppressed while `error` is set: "No articles found — try a different
            search" is the wrong advice when the request never succeeded. */}
        {!loading && !error && filteredArticles.length === 0 && (
          <div className="text-center py-12">
            <Book className="w-12 h-12 mx-auto text-gray-600 mb-4" />
            <p className="text-gray-400">No articles found</p>
            <p className="text-sm text-gray-500">
              {showStarredOnly
                ? "Nothing starred yet."
                : "Try a different search or category"}
            </p>
          </div>
        )}
      </div>

      {/* Article Viewer Modal */}
      <AnimatePresence>
        {selectedArticle && (
          <ArticleViewer
            key={selectedArticle.id}
            article={selectedArticle}
            onClose={() => setSelectedArticle(null)}
            onSave={(patch) => handleUpdateArticle(selectedArticle.id, patch)}
            saving={saving}
          />
        )}
      </AnimatePresence>

      {/* Create Article Modal */}
      <AnimatePresence>
        {showCreateModal && (
          <CreateArticleModal
            onClose={() => setShowCreateModal(false)}
            onSave={handleCreateArticle}
            saving={saving}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
