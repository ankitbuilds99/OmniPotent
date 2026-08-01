import { useState } from "react";
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
} from "lucide-react";

interface KnowledgeArticle {
  id: string;
  title: string;
  content: string;
  category: "runbook" | "glossary" | "best-practice" | "troubleshooting" | "architecture";
  tags: string[];
  author: string;
  createdAt: string;
  updatedAt: string;
  starred: boolean;
  linkedServices: string[];
}

const SAMPLE_ARTICLES: KnowledgeArticle[] = [
  {
    id: "1",
    title: "Database Connection Pool Exhaustion",
    content: `## Symptoms
- High latency on database queries
- Errors: "Connection pool exhausted"
- Increasing queue depth in metrics

## Root Cause Analysis
Connection pool exhaustion typically occurs when:
1. Connection leaks (not returning connections)
2. Long-running queries holding connections
3. Pool size too small for load

## Resolution Steps
1. Check current pool usage: \`SELECT count(*) FROM pg_stat_activity\`
2. Identify long-running queries
3. If urgent: Restart the affected service
4. For permanent fix: Increase pool size or fix leaks

## Prevention
- Set connection timeouts
- Use connection pool monitoring
- Regular load testing`,
    category: "troubleshooting",
    tags: ["database", "postgres", "performance"],
    author: "Platform Team",
    createdAt: "2024-01-15",
    updatedAt: "2024-03-20",
    starred: true,
    linkedServices: ["payment-service", "checkout-service"],
  },
  {
    id: "2",
    title: "Kubernetes Pod Crash Loop",
    content: `## Definition
A CrashLoopBackOff occurs when a container repeatedly fails to start.

## Debugging Steps
1. Get pod status: \`kubectl describe pod <name>\`
2. Check logs: \`kubectl logs <pod> --previous\`
3. Check events: \`kubectl get events --sort-by=.metadata.creationTimestamp\`

## Common Causes
- Missing config/secrets
- Resource limits too low
- Image pull failures
- Application startup errors

## Quick Fixes
- Increase memory limits
- Fix readiness/liveness probes
- Check image tag exists`,
    category: "runbook",
    tags: ["kubernetes", "k8s", "pods", "debugging"],
    author: "SRE Team",
    createdAt: "2024-02-10",
    updatedAt: "2024-02-10",
    starred: false,
    linkedServices: [],
  },
  {
    id: "3",
    title: "MTTR - Mean Time To Recovery",
    content: `## Definition
MTTR measures the average time between when an incident is detected and when service is restored.

## Formula
MTTR = Total Downtime / Number of Incidents

## Target
- Tier 1 services: < 15 minutes
- Tier 2 services: < 30 minutes
- Tier 3 services: < 2 hours

## How to Improve
1. Better monitoring and alerting
2. Automated runbooks
3. Clear escalation paths
4. Regular incident drills`,
    category: "glossary",
    tags: ["metrics", "sre", "reliability"],
    author: "SRE Team",
    createdAt: "2024-01-05",
    updatedAt: "2024-01-05",
    starred: true,
    linkedServices: [],
  },
  {
    id: "4",
    title: "Incident Response Best Practices",
    content: `## Golden Rules
1. **Communicate early and often** - Update stakeholders every 15 minutes
2. **Mitigate first, investigate later** - Stop the bleeding
3. **Don't go alone** - Page help if needed
4. **Document everything** - Timeline, actions, findings

## Roles
- **Incident Commander**: Coordinates response
- **Technical Lead**: Hands-on debugging
- **Communications Lead**: Stakeholder updates

## Post-Incident
- Write blameless postmortem within 48 hours
- Identify action items
- Share learnings`,
    category: "best-practice",
    tags: ["incident", "response", "process"],
    author: "Platform Team",
    createdAt: "2024-02-01",
    updatedAt: "2024-03-15",
    starred: false,
    linkedServices: [],
  },
  {
    id: "5",
    title: "Payment Service Architecture",
    content: `## Overview
The payment service handles all payment processing through our gateway.

## Components
- **API Layer**: FastAPI REST endpoints
- **Queue**: Redis for async processing
- **Gateway**: Stripe integration
- **Database**: PostgreSQL for transactions

## Flow
1. Client submits payment request
2. API validates and enqueues
3. Worker processes payment via Stripe
4. Result stored and webhook sent

## Dependencies
- Stripe API
- Redis cluster
- PostgreSQL primary
- Notification service`,
    category: "architecture",
    tags: ["payment", "stripe", "architecture"],
    author: "Engineering",
    createdAt: "2024-01-20",
    updatedAt: "2024-03-01",
    starred: false,
    linkedServices: ["payment-service"],
  },
];

const CATEGORIES = [
  { id: "all", label: "All", icon: <FolderOpen className="w-4 h-4" /> },
  { id: "runbook", label: "Runbooks", icon: <FileText className="w-4 h-4" /> },
  { id: "glossary", label: "Glossary", icon: <BookOpen className="w-4 h-4" /> },
  { id: "best-practice", label: "Best Practices", icon: <CheckCircle className="w-4 h-4" /> },
  { id: "troubleshooting", label: "Troubleshooting", icon: <AlertCircle className="w-4 h-4" /> },
  { id: "architecture", label: "Architecture", icon: <Code className="w-4 h-4" /> },
];

function ArticleCard({
  article,
  onSelect,
  onToggleStar,
}: {
  article: KnowledgeArticle;
  onSelect: () => void;
  onToggleStar: () => void;
}) {
  const categoryIcons: Record<string, React.ReactNode> = {
    runbook: <FileText className="w-4 h-4" />,
    glossary: <BookOpen className="w-4 h-4" />,
    "best-practice": <CheckCircle className="w-4 h-4" />,
    troubleshooting: <AlertCircle className="w-4 h-4" />,
    architecture: <Code className="w-4 h-4" />,
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
            {categoryIcons[article.category]}
          </div>
          <span className="text-xs px-2 py-0.5 rounded-full capitalize" style={{ backgroundColor: "var(--panel)" }}>
            {article.category.replace("-", " ")}
          </span>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggleStar();
          }}
          className="p-1 hover:bg-white/10 rounded"
        >
          {article.starred ? (
            <Star className="w-4 h-4 text-yellow-500 fill-yellow-500" />
          ) : (
            <StarOff className="w-4 h-4 text-gray-500" />
          )}
        </button>
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
          {article.author}
        </div>
        <div className="flex items-center gap-1">
          <Clock className="w-3 h-3" />
          Updated {article.updatedAt}
        </div>
      </div>
    </motion.div>
  );
}

function ArticleViewer({
  article,
  onClose,
  onEdit,
}: {
  article: KnowledgeArticle;
  onClose: () => void;
  onEdit: () => void;
}) {
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
          <div>
            <h2 className="text-xl font-semibold">{article.title}</h2>
            <div className="flex items-center gap-4 mt-2 text-sm text-gray-400">
              <span className="flex items-center gap-1">
                <User className="w-4 h-4" />
                {article.author}
              </span>
              <span className="flex items-center gap-1">
                <Clock className="w-4 h-4" />
                Updated {article.updatedAt}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onEdit}
              className="p-2 rounded-lg hover:bg-white/10"
              title="Edit"
            >
              <Edit3 className="w-5 h-5" />
            </button>
            <button
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-white/10"
            >
              <span className="text-xl">&times;</span>
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
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

          {article.linkedServices.length > 0 && (
            <div className="mb-6 p-3 rounded-lg" style={{ backgroundColor: "var(--shell)" }}>
              <div className="text-sm font-medium mb-2 flex items-center gap-2">
                <LinkIcon className="w-4 h-4" />
                Linked Services
              </div>
              <div className="flex flex-wrap gap-2">
                {article.linkedServices.map((service) => (
                  <span
                    key={service}
                    className="text-sm px-2 py-1 rounded bg-blue-500/20 text-blue-400"
                  >
                    {service}
                  </span>
                ))}
              </div>
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
        </div>
      </motion.div>
    </div>
  );
}

function CreateArticleModal({ onClose, onSave }: { onClose: () => void; onSave: (article: Partial<KnowledgeArticle>) => void }) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState<KnowledgeArticle["category"]>("runbook");
  const [tags, setTags] = useState("");

  const handleSave = () => {
    onSave({
      title,
      content,
      category,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
    });
    onClose();
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

        <div className="p-6 space-y-4">
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

          <div>
            <label className="block text-sm font-medium mb-2">Category</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as KnowledgeArticle["category"])}
              className="w-full px-4 py-2 rounded-lg border"
              style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
            >
              <option value="runbook">Runbook</option>
              <option value="glossary">Glossary</option>
              <option value="best-practice">Best Practice</option>
              <option value="troubleshooting">Troubleshooting</option>
              <option value="architecture">Architecture</option>
            </select>
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
            disabled={!title || !content}
            className="px-6 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Create Article
          </button>
        </div>
      </motion.div>
    </div>
  );
}

export function KnowledgeBase({ onBack }: { onBack: () => void }) {
  const [articles, setArticles] = useState<KnowledgeArticle[]>(SAMPLE_ARTICLES);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [selectedArticle, setSelectedArticle] = useState<KnowledgeArticle | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showStarredOnly, setShowStarredOnly] = useState(false);

  const filteredArticles = articles.filter((article) => {
    const matchesSearch =
      article.title.toLowerCase().includes(search.toLowerCase()) ||
      article.content.toLowerCase().includes(search.toLowerCase()) ||
      article.tags.some((tag) => tag.toLowerCase().includes(search.toLowerCase()));
    const matchesCategory = category === "all" || article.category === category;
    const matchesStarred = !showStarredOnly || article.starred;
    return matchesSearch && matchesCategory && matchesStarred;
  });

  const toggleStar = (id: string) => {
    setArticles((prev) =>
      prev.map((a) => (a.id === id ? { ...a, starred: !a.starred } : a))
    );
  };

  const handleCreateArticle = (newArticle: Partial<KnowledgeArticle>) => {
    const article: KnowledgeArticle = {
      id: Date.now().toString(),
      title: newArticle.title || "",
      content: newArticle.content || "",
      category: newArticle.category || "runbook",
      tags: newArticle.tags || [],
      author: "You",
      createdAt: new Date().toISOString().split("T")[0],
      updatedAt: new Date().toISOString().split("T")[0],
      starred: false,
      linkedServices: [],
    };
    setArticles((prev) => [article, ...prev]);
  };

  return (
    <div className="min-h-screen" style={{ backgroundColor: "var(--bg)" }}>
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
                  <p className="text-sm text-gray-400">{articles.length} articles</p>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3">
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

        {/* Articles Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredArticles.map((article) => (
            <ArticleCard
              key={article.id}
              article={article}
              onSelect={() => setSelectedArticle(article)}
              onToggleStar={() => toggleStar(article.id)}
            />
          ))}
        </div>

        {filteredArticles.length === 0 && (
          <div className="text-center py-12">
            <Book className="w-12 h-12 mx-auto text-gray-600 mb-4" />
            <p className="text-gray-400">No articles found</p>
            <p className="text-sm text-gray-500">Try a different search or category</p>
          </div>
        )}
      </div>

      {/* Article Viewer Modal */}
      <AnimatePresence>
        {selectedArticle && (
          <ArticleViewer
            article={selectedArticle}
            onClose={() => setSelectedArticle(null)}
            onEdit={() => {
              // Would open edit modal
              setSelectedArticle(null);
            }}
          />
        )}
      </AnimatePresence>

      {/* Create Article Modal */}
      <AnimatePresence>
        {showCreateModal && (
          <CreateArticleModal
            onClose={() => setShowCreateModal(false)}
            onSave={handleCreateArticle}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
