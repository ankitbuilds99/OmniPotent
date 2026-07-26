import { motion } from 'framer-motion'
import {
  Calendar,
  Clock,
  ArrowRight,
  Tag,
  User,
  TrendingUp,
  Cpu,
  GitMerge,
  Lightbulb
} from 'lucide-react'

const BLOG_POSTS = [
  {
    id: 1,
    title: 'Building an AI-Powered DevOps Copilot with LangGraph',
    excerpt: 'Learn how we built an intelligent agent that can investigate incidents, analyze logs, and even create pull requests - all powered by LLMs and a graph-based workflow engine.',
    author: 'Ankit Pandey',
    date: '2024-07-20',
    readTime: '8 min read',
    category: 'Engineering',
    icon: Cpu,
    color: '#00E7FF',
    featured: true,
    tags: ['AI', 'LangGraph', 'DevOps', 'Python'],
    url: 'https://github.com/ankit25bcs10610/DevOps-Copilot#readme'
  },
  {
    id: 2,
    title: 'From 4 Hours to 4 Minutes: Automating Incident Response',
    excerpt: 'How AI-driven root cause analysis reduced our mean time to resolution (MTTR) by 98%. A deep dive into the architecture and lessons learned.',
    author: 'Ankit Pandey',
    date: '2024-07-15',
    readTime: '6 min read',
    category: 'Case Study',
    icon: TrendingUp,
    color: '#00FFB3',
    featured: false,
    tags: ['Incident Response', 'Automation', 'Metrics'],
    url: 'https://github.com/ankit25bcs10610/DevOps-Copilot#features'
  },
  {
    id: 3,
    title: 'Human-in-the-Loop: Why AI Needs Guardrails',
    excerpt: 'Exploring the importance of human oversight in AI-powered DevOps automation. How we designed approval workflows that balance speed with safety.',
    author: 'Ankit Pandey',
    date: '2024-07-10',
    readTime: '5 min read',
    category: 'Best Practices',
    icon: Lightbulb,
    color: '#FF9900',
    featured: false,
    tags: ['AI Safety', 'Best Practices', 'Security'],
    url: 'https://github.com/ankit25bcs10610/DevOps-Copilot#human-in-the-loop-approval'
  },
  {
    id: 4,
    title: 'Integrating LLMs with Datadog, PagerDuty, and GitHub',
    excerpt: 'A technical guide to building integrations that let your AI agent read logs, acknowledge alerts, and submit code changes across your DevOps toolchain.',
    author: 'Ankit Pandey',
    date: '2024-07-05',
    readTime: '10 min read',
    category: 'Tutorial',
    icon: GitMerge,
    color: '#7C3AED',
    featured: false,
    tags: ['Integration', 'API', 'Tutorial'],
    url: 'https://github.com/ankit25bcs10610/DevOps-Copilot/tree/main/app/mcp'
  }
]

function FeaturedPost({ post }: { post: typeof BLOG_POSTS[0] }) {
  const Icon = post.icon

  return (
    <a
      href={post.url}
      target="_blank"
      rel="noreferrer"
      className="block"
    >
      <motion.article
        className="group relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-white/5 to-transparent hover:border-primary/30 transition-all duration-300 cursor-pointer"
        initial={{ opacity: 0, y: 40 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.5 }}
        whileHover={{ y: -5 }}
      >
      {/* Featured badge */}
      <div className="absolute top-4 left-4 z-10">
        <span className="px-3 py-1 bg-primary/20 text-primary text-xs font-semibold rounded-full border border-primary/30">
          Featured
        </span>
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        {/* Image placeholder with icon */}
        <div
          className="aspect-video md:aspect-auto md:h-full min-h-[250px] flex items-center justify-center"
          style={{ background: `linear-gradient(135deg, ${post.color}10, ${post.color}05)` }}
        >
          <div
            className="w-24 h-24 rounded-2xl flex items-center justify-center"
            style={{ backgroundColor: `${post.color}20` }}
          >
            <Icon className="w-12 h-12" style={{ color: post.color }} />
          </div>
        </div>

        {/* Content */}
        <div className="p-6 md:p-8 flex flex-col justify-center">
          <div className="flex items-center gap-3 mb-4">
            <span
              className="px-2 py-1 rounded text-xs font-medium"
              style={{ backgroundColor: `${post.color}20`, color: post.color }}
            >
              {post.category}
            </span>
            <span className="flex items-center gap-1 text-white/40 text-sm">
              <Clock className="w-3 h-3" />
              {post.readTime}
            </span>
          </div>

          <h3 className="text-2xl font-bold text-white mb-3 group-hover:text-primary transition-colors">
            {post.title}
          </h3>

          <p className="text-white/60 mb-6 line-clamp-3">
            {post.excerpt}
          </p>

          <div className="flex items-center justify-between mt-auto">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center">
                <User className="w-4 h-4 text-primary" />
              </div>
              <div>
                <p className="text-white text-sm font-medium">{post.author}</p>
                <p className="text-white/40 text-xs flex items-center gap-1">
                  <Calendar className="w-3 h-3" />
                  {new Date(post.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                </p>
              </div>
            </div>

            <span className="flex items-center gap-1 text-primary text-sm font-medium group-hover:gap-2 transition-all">
              Read more
              <ArrowRight className="w-4 h-4" />
            </span>
          </div>
        </div>
      </div>
      </motion.article>
    </a>
  )
}

function BlogCard({ post, index }: { post: typeof BLOG_POSTS[0]; index: number }) {
  const Icon = post.icon

  return (
    <a
      href={post.url}
      target="_blank"
      rel="noreferrer"
      className="block"
    >
      <motion.article
        className="group relative overflow-hidden rounded-xl border border-white/10 bg-white/[0.02] hover:border-primary/30 hover:bg-white/[0.04] transition-all duration-300 cursor-pointer h-full"
        initial={{ opacity: 0, y: 30 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.4, delay: index * 0.1 }}
        whileHover={{ y: -3 }}
      >
      {/* Header with icon */}
      <div
        className="h-32 flex items-center justify-center"
        style={{ background: `linear-gradient(135deg, ${post.color}08, transparent)` }}
      >
        <div
          className="w-14 h-14 rounded-xl flex items-center justify-center"
          style={{ backgroundColor: `${post.color}15` }}
        >
          <Icon className="w-7 h-7" style={{ color: post.color }} />
        </div>
      </div>

      {/* Content */}
      <div className="p-5">
        <div className="flex items-center gap-2 mb-3">
          <span
            className="px-2 py-0.5 rounded text-xs font-medium"
            style={{ backgroundColor: `${post.color}15`, color: post.color }}
          >
            {post.category}
          </span>
          <span className="text-white/40 text-xs">{post.readTime}</span>
        </div>

        <h3 className="font-semibold text-white mb-2 group-hover:text-primary transition-colors line-clamp-2">
          {post.title}
        </h3>

        <p className="text-white/50 text-sm mb-4 line-clamp-2">
          {post.excerpt}
        </p>

        {/* Tags */}
        <div className="flex flex-wrap gap-1 mb-4">
          {post.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 px-2 py-0.5 bg-white/5 rounded text-xs text-white/50"
            >
              <Tag className="w-2.5 h-2.5" />
              {tag}
            </span>
          ))}
        </div>

        <div className="flex items-center justify-between pt-4 border-t border-white/5">
          <span className="text-white/40 text-xs flex items-center gap-1">
            <Calendar className="w-3 h-3" />
            {new Date(post.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
          </span>
          <span className="text-primary text-xs font-medium flex items-center gap-1 group-hover:gap-2 transition-all">
            Read
            <ArrowRight className="w-3 h-3" />
          </span>
        </div>
      </div>
      </motion.article>
    </a>
  )
}

export function Blog() {
  const featuredPost = BLOG_POSTS.find(p => p.featured)
  const otherPosts = BLOG_POSTS.filter(p => !p.featured)

  return (
    <section id="blog" className="relative py-32 overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-background to-[#030810]" />

      {/* Decorative elements */}
      <div className="absolute top-1/4 -left-32 w-64 h-64 bg-primary/5 rounded-full blur-3xl" />
      <div className="absolute bottom-1/4 -right-32 w-64 h-64 bg-purple-500/5 rounded-full blur-3xl" />

      <div className="container-wide relative z-10">
        {/* Header */}
        <div className="text-center mb-16">
          <motion.span
            className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
          >
            Blog
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
          >
            Insights & <span className="gradient-text">Updates</span>
          </motion.h2>
          <motion.p
            className="text-white/50 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.2 }}
          >
            Technical deep-dives, tutorials, and thoughts on AI-powered DevOps
          </motion.p>
        </div>

        {/* Featured post */}
        {featuredPost && (
          <div className="mb-12">
            <FeaturedPost post={featuredPost} />
          </div>
        )}

        {/* Other posts grid */}
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
          {otherPosts.map((post, i) => (
            <BlogCard key={post.id} post={post} index={i} />
          ))}
        </div>

        {/* Newsletter CTA */}
        <motion.div
          className="mt-16 text-center"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
        >
          <div className="inline-flex flex-col sm:flex-row items-center gap-4 p-6 bg-white/5 rounded-2xl border border-white/10">
            <div className="text-left">
              <h4 className="font-semibold text-white mb-1">Stay updated</h4>
              <p className="text-white/50 text-sm">Get notified about new posts and features</p>
            </div>
            <a
              href="mailto:ankit25bcs10610@gmail.com?subject=Subscribe to DevOps Copilot updates"
              className="px-6 py-3 bg-primary/20 hover:bg-primary/30 text-primary font-medium rounded-xl border border-primary/30 transition-colors whitespace-nowrap"
            >
              Subscribe via Email
            </a>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
