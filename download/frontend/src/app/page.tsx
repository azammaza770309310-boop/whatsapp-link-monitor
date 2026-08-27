'use client'

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import {
  MessageCircle, Send, Search, Users, Link2,
  RefreshCw, ExternalLink, Phone, MapPin, Clock, ArrowRight,
  X, Activity, CheckCircle2, XCircle, AlertTriangle, Clock3,
  Copy, Check, Download, TrendingUp, BarChart3, Flame,
  VolumeX, ShieldCheck,
} from 'lucide-react'
import type { ReactNode, MouseEvent as ReactMouseEvent, ChangeEvent } from 'react'

// ===== Types =====
interface LinkItem {
  id: number
  link: string
  link_type: string
  message_text: string | null
  group_name: string | null
  sender_name: string | null
  sender_contact: string | null
  source_phone: string | null
  message_link: string | null
  created_at: string
  ai_approved?: boolean | null
  ai_description?: string | null
  ai_country?: string | null
  ai_is_ad?: boolean | null
}

interface AIStats {
  ai_approved: number
  ai_rejected: number
  ai_ads: number
  ai_pending: number
  ai_batch_mode: boolean
}

interface Stats {
  total_links: number
  whatsapp_links: number
  telegram_links: number
  active_watchers: number
  ai_stats?: AIStats
}

interface Joiner {
  phone: string
  display_name: string
  connected: boolean
  daily_joins: number
  daily_limit: number
  last_join_timestamp: string
  joiner_enabled: number
}

interface JoinersSummary {
  total_joiners: number
  connected_joiners: number
  total_joined_groups: number
  total_already_member: number
  total_banned: number
}

interface JoinedGroup {
  group_link: string
  group_title: string
  state: string
  joined_by_phone: string
  member_count: number
  join_date: string
}

interface MonitoredChat {
  chat_id: number
  chat_title: string
  username: string
  link_type: string
  monitored_by: string
  member_count: number
  ai_classification: string
  ai_country: string
  ai_relevance: number
  ai_description: string
  should_monitor: number
  first_seen: string
  last_seen: string
}

interface MonitoredSummary {
  total: number
  classified: number
  unclassified: number
  educational: number
  high_relevance: number
  by_country: Record<string, number>
  by_type: Record<string, number>
}

interface BannedGroup {
  group_link: string
  group_title: string
  state: string
  joined_by_phone: string
  member_count: number
  join_date: string
  last_error: string
}

// [FLEET-VIEW] /ready response — ungated health endpoint. fleet_health is
// the live joiner-fleet snapshot (REQAUDIT-3): without it the operator
// couldn't see that ALL joiners were in FloodWait while /ready said "ready".
interface FleetHealth {
  connected_joiners: number
  floodwait_joiners_count: number
  disconnected_joiners_count: number
  safety_guard_blocked_joiners: number
  all_joiners_unavailable: boolean
}

interface ReadinessData {
  status: string
  bot_connected: boolean
  db_connected: boolean
  active_watchers: number
  scan_running: boolean
  fleet_health?: FleetHealth
}

// [PENDING-VIEW] /api/pending_approvals response — groups where a join
// request was sent and we're awaiting admin approval (REQAUDIT-2). The
// 30-min self-healing recheck loop flips them to JOINED on approval.
interface PendingApproval {
  id: number
  group_link: string
  status: string
  joined_by_phone: string
  since: string
  last_error: string
}

interface PendingApprovalsSummary {
  total_pending_approval: number
  recheck_interval_seconds: number
  self_healing: boolean
}

// [TREND-VIEW] /api/links_daily response — daily capture counts for the
// trend chart. `other` is included for completeness (rare link types).
interface DailyStat {
  date: string
  whatsapp: number
  telegram: number
  other: number
  total: number
}

interface TrendTotals {
  total: number
  whatsapp: number
  telegram: number
  best_day: { date: string; count: number } | null
  avg_per_day: number
  peak_hour?: { hour: number; count: number } | null
}

// [HEATMAP-VIEW] hour-of-day buckets from /api/links_daily — shows WHEN
// links get posted (24 UTC buckets, peak-hour highlighted).
interface HourlyStat {
  hour: number
  whatsapp: number
  telegram: number
  other: number
  total: number
}

// [SOURCE-VIEW] /api/top_groups response — link-source attribution: which
// groups produced the captured links (WA/TG split + share of window).
interface TopGroup {
  group: string
  total: number
  whatsapp: number
  telegram: number
  other: number
  share: number
  first_seen: string
  last_seen: string
}

interface TopGroupsTotals {
  total: number
  distinct_groups: number
}

// [SOURCE-HEALTH] a producing source whose last_seen is aging — the client
// computes daysQuiet from the 30-day top-groups snapshot.
type QuietSource = TopGroup & { daysQuiet: number }

type ModalType =
  | 'whatsapp'
  | 'telegram'
  | 'all_links'
  | 'ai_approved'
  | 'ai_rejected'
  | 'ai_ads'
  | 'joiners'
  | 'joined_groups'
  | 'monitored_chats'
  | 'banned_groups'
  | 'pending_approvals'
  | 'top_groups'
  | null

// ===== Constants =====
const API_URL: string =
  process.env.NEXT_PUBLIC_API_URL || 'https://whatsapp-userbot-yzm7.onrender.com'

// [DASHBOARD-RESTORE] Optional shared-secret — if the operator sets
// DASHBOARD_API_KEY on Render AND mirrors it here as
// NEXT_PUBLIC_DASHBOARD_API_KEY on Vercel, the dashboard sends the
// X-Api-Key header (defense-in-depth). When unset, the dashboard
// relies on the backend's DASHBOARD_ALLOWED_ORIGINS allowlist.
const API_KEY: string | undefined = process.env.NEXT_PUBLIC_DASHBOARD_API_KEY

function buildHeaders(): Record<string, string> {
  const h: Record<string, string> = { Accept: 'application/json' }
  if (API_KEY) h['X-Api-Key'] = API_KEY
  return h
}

// [SOURCE-HEALTH] quiet-source detection thresholds — a source that
// produced links within the last 30 days but has been silent for 2+ days
// is "quieting"; 5+ days means it effectively stopped (watcher removed,
// group went private, or the audience moved on).
const HEALTH_WINDOW_DAYS = 30
const QUIET_AFTER_DAYS = 2
const STOPPED_AFTER_DAYS = 5

function safeUrl(url: string | null | undefined): string | null {
  if (!url || typeof url !== 'string') return null
  const trimmed = url.trim()
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return null
}

// ===== Main Component =====
export default function Home() {
  const [allLinks, setAllLinks] = useState<LinkItem[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [joiners, setJoiners] = useState<Joiner[]>([])
  const [joinersSummary, setJoinersSummary] = useState<JoinersSummary | null>(null)
  const [joinedGroups, setJoinedGroups] = useState<JoinedGroup[]>([])
  const [monitoredChats, setMonitoredChats] = useState<MonitoredChat[]>([])
  const [monitoredSummary, setMonitoredSummary] = useState<MonitoredSummary | null>(null)
  const [bannedGroups, setBannedGroups] = useState<BannedGroup[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [modal, setModal] = useState<ModalType>(null)
  // [DASHBOARD-RESTORE] Visible error state — replaces the old "silent
  // catch" that left the operator staring at a blank dashboard with no
  // clue why every card was empty.
  const [apiError, setApiError] = useState<string | null>(null)
  // [FLEET-VIEW + PENDING-VIEW + LIVE-STATUS] new operational data
  const [readiness, setReadiness] = useState<ReadinessData | null>(null)
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([])
  const [pendingSummary, setPendingSummary] = useState<PendingApprovalsSummary | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [now, setNow] = useState<Date>(new Date())
  // [TREND-VIEW] daily capture trend (60s poll — server caches 60s too)
  const [trendDaily, setTrendDaily] = useState<DailyStat[]>([])
  const [trendTotals, setTrendTotals] = useState<TrendTotals | null>(null)
  // [TREND-VIEW] window size (7/14/30) — shared by the trend chart, the
  // hourly strip AND the top-groups card so all views stay in sync.
  const [trendDays, setTrendDays] = useState<number>(14)
  // [HEATMAP-VIEW] hour-of-day activity buckets
  const [hourly, setHourly] = useState<HourlyStat[]>([])
  // [SOURCE-VIEW] link-source attribution (top producing groups)
  const [topGroups, setTopGroups] = useState<TopGroup[]>([])
  const [topGroupsTotals, setTopGroupsTotals] = useState<TopGroupsTotals | null>(null)
  // [SOURCE-HEALTH] independent 30-day top-groups snapshot for quiet-source
  // detection (NOT tied to trendDays — see fetchSourceHealth rationale).
  const [healthGroups, setHealthGroups] = useState<TopGroup[]>([])

  // ===== Fetch Functions =====
  const fetchStats = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/stats`, {
        headers: buildHeaders(),
      })
      if (response.ok) {
        const data = await response.json()
        setStats({
          total_links: data.total_links || 0,
          whatsapp_links: data.whatsapp_links || 0,
          telegram_links: data.telegram_links || 0,
          active_watchers: data.active_watchers || 0,
          ai_stats: data.ai_stats
            ? {
                ai_approved: data.ai_stats.ai_approved || 0,
                ai_rejected: data.ai_stats.ai_rejected || 0,
                ai_ads: data.ai_stats.ai_ads || 0,
                ai_pending: data.ai_stats.ai_pending || 0,
                ai_batch_mode: !!data.ai_stats.ai_batch_mode,
              }
            : undefined,
        })
        setApiError(null)
      } else if (response.status === 401) {
        setApiError(
          'فشل الاتصال بالخادم (401): مفتاح API غير مُهيّأ. راجع DASHBOARD_ALLOWED_ORIGINS في Render، أو اضبط NEXT_PUBLIC_DASHBOARD_API_KEY في Vercel.'
        )
      } else {
        setApiError(`فشل تحميل الإحصائيات (HTTP ${response.status})`)
      }
    } catch (err) {
      setApiError('تعذّر الوصول إلى الخادم — تحقق من اتصال الإنترنت أو حالة Render.')
    }
  }, [])

  const fetchLinks = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/links?limit=5000`, {
        headers: buildHeaders(),
      })
      if (response.ok) {
        const data = await response.json()
        const links: LinkItem[] = data.links || []
        if (Array.isArray(links)) {
          setAllLinks(links)
          setLoading(false)
        }
        setApiError(null)
      } else if (response.status === 401) {
        setLoading(false)
        setApiError(
          'فشل تحميل الروابط (401): الخادم يرفض الطلب. فعّل DASHBOARD_ALLOWED_ORIGINS في Render أو اضبط مفتاح API.'
        )
      } else {
        setLoading(false)
        setApiError(`فشل تحميل الروابط (HTTP ${response.status})`)
      }
    } catch (err) {
      setLoading(false)
      setApiError('تعذّر الوصول إلى الخادم — تحقق من اتصال الإنترنت أو حالة Render.')
    }
  }, [])

  const fetchJoiners = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/joiners_status`, {
        headers: buildHeaders(),
      })
      if (response.ok) {
        const data = await response.json()
        setJoiners(data.joiners || [])
        setJoinersSummary(data.summary || null)
        setJoinedGroups(data.joined_groups || [])
        setBannedGroups(data.banned_groups || [])
      } else if (response.status === 401) {
        setApiError(
          'فشل تحميل بيانات الحسابات (401): الخادم يرفض الطلب — راجع إعدادات DASHBOARD_ALLOWED_ORIGINS.'
        )
      } else {
        console.error('fetchJoiners HTTP error:', response.status)
      }
    } catch (err) {
      console.error('fetchJoiners error:', err)
    }
  }, [])

  const fetchMonitoredChats = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/monitored_chats`, {
        headers: buildHeaders(),
      })
      if (response.ok) {
        const data = await response.json()
        setMonitoredChats(data.chats || [])
        setMonitoredSummary(data.summary || null)
      } else if (response.status === 401) {
        setApiError(
          'فشل تحميل المجموعات المراقبة (401): الخادم يرفض الطلب — راجع إعدادات DASHBOARD_ALLOWED_ORIGINS.'
        )
      } else {
        console.error('fetchMonitoredChats HTTP error:', response.status)
      }
    } catch (err) {
      console.error('fetchMonitoredChats error:', err)
    }
  }, [])

  // [FLEET-VIEW] /ready is UNGATED (health probe) → works even when
  // DASHBOARD_API_KEY blocks /api/* — the fleet card always shows live data.
  const fetchReadiness = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/ready`, {
        headers: { Accept: 'application/json' },
      })
      if (response.ok) {
        const data = await response.json()
        setReadiness({
          status: data.status || 'unknown',
          bot_connected: !!data.bot_connected,
          db_connected: !!data.db_connected,
          active_watchers: data.active_watchers || 0,
          scan_running: !!data.scan_running,
          fleet_health: data.fleet_health
            ? {
                connected_joiners: data.fleet_health.connected_joiners || 0,
                floodwait_joiners_count: data.fleet_health.floodwait_joiners_count || 0,
                disconnected_joiners_count: data.fleet_health.disconnected_joiners_count || 0,
                safety_guard_blocked_joiners: data.fleet_health.safety_guard_blocked_joiners || 0,
                all_joiners_unavailable: !!data.fleet_health.all_joiners_unavailable,
              }
            : undefined,
        })
      }
    } catch (err) {
      console.error('fetchReadiness error:', err)
    }
  }, [])

  // [PENDING-VIEW] groups awaiting admin approval (REQAUDIT-2 lifecycle).
  const fetchPendingApprovals = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/pending_approvals`, {
        headers: buildHeaders(),
      })
      if (response.ok) {
        const data = await response.json()
        setPendingApprovals(data.pending_approvals || [])
        setPendingSummary(data.stats || null)
      }
    } catch (err) {
      console.error('fetchPendingApprovals error:', err)
    }
  }, [])

  // [TREND-VIEW] daily capture trend + [HEATMAP-VIEW] hourly buckets —
  // 60s cadence (NOT 15s): the backend caches the aggregation for 60s and
  // a 14-day window can scan ~28K Supabase rows; polling faster would only
  // burn quota without fresher data. Parametrized by window size so the
  // 7/14/30 selector can refetch without recreating the callback.
  const fetchTrend = useCallback(async (days: number) => {
    try {
      const response = await fetch(`${API_URL}/api/links_daily?days=${days}`, {
        headers: buildHeaders(),
      })
      if (response.ok) {
        const data = await response.json()
        if (Array.isArray(data.daily)) setTrendDaily(data.daily)
        if (Array.isArray(data.hourly)) setHourly(data.hourly)
        if (data.totals) setTrendTotals(data.totals)
      }
    } catch (err) {
      console.error('fetchTrend error:', err)
    }
  }, [])

  // [SOURCE-VIEW] top link-producing groups over the same window — answers
  // "which sources produce the most links" (and, when capture drops, which
  // sources went quiet). limit=50 (server max) so the full modal list is
  // available; the card slices the top 6.
  const fetchTopGroups = useCallback(async (days: number) => {
    try {
      const response = await fetch(
        `${API_URL}/api/top_groups?days=${days}&limit=50`,
        { headers: buildHeaders() }
      )
      if (response.ok) {
        const data = await response.json()
        if (Array.isArray(data.groups)) setTopGroups(data.groups)
        if (data.totals) setTopGroupsTotals(data.totals)
      }
    } catch (err) {
      console.error('fetchTopGroups error:', err)
    }
  }, [])

  // [SOURCE-HEALTH] 30-day top-groups snapshot for quiet-source detection.
  // Deliberately independent of the trend window (trendDays): a source that
  // stopped 20 days ago is invisible in a 7/14-day window — only the widest
  // window reliably detects it. The server caches per (days,limit) key for
  // 60s, so this extra poll is cheap.
  const fetchSourceHealth = useCallback(async () => {
    try {
      const response = await fetch(
        `${API_URL}/api/top_groups?days=${HEALTH_WINDOW_DAYS}&limit=50`,
        { headers: buildHeaders() }
      )
      if (response.ok) {
        const data = await response.json()
        if (Array.isArray(data.groups)) setHealthGroups(data.groups)
      }
    } catch (err) {
      console.error('fetchSourceHealth error:', err)
    }
  }, [])

  // Initial load + auto refresh (real-time every 15s)
  useEffect(() => {
    const load = async () => {
      await Promise.all([
        fetchLinks(),
        fetchStats(),
        fetchJoiners(),
        fetchMonitoredChats(),
        fetchReadiness(),
        fetchPendingApprovals(),
      ])
      setLastUpdated(new Date())
    }
    load()
    const interval = setInterval(load, 15000) // 15s for real-time updates
    return () => clearInterval(interval)
  }, [fetchLinks, fetchStats, fetchJoiners, fetchMonitoredChats, fetchReadiness, fetchPendingApprovals])

  // [LIVE-STATUS] 1s ticker so "قبل X ثانية" freshness stays live between
  // the 15s data refreshes.
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  // [TREND-VIEW] initial load + 60s refresh cycle (see fetchTrend rationale).
  // Re-runs when the window selector changes (7/14/30) so the chart, the
  // hourly strip and the top-groups card all switch windows together.
  useEffect(() => {
    const load = () => {
      fetchTrend(trendDays)
      fetchTopGroups(trendDays)
      fetchSourceHealth()
    }
    load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [fetchTrend, fetchTopGroups, fetchSourceHealth, trendDays])

  const refreshAll = useCallback(() => {
    fetchLinks()
    fetchStats()
    fetchJoiners()
    fetchMonitoredChats()
    fetchReadiness()
    fetchPendingApprovals()
    fetchTrend(trendDays)
    fetchTopGroups(trendDays)
    fetchSourceHealth()
    setLastUpdated(new Date())
  }, [fetchLinks, fetchStats, fetchJoiners, fetchMonitoredChats, fetchReadiness, fetchPendingApprovals, fetchTrend, fetchTopGroups, fetchSourceHealth, trendDays])

  // [LIVE-STATUS] seconds since last successful refresh
  const secondsAgo = lastUpdated
    ? Math.max(0, Math.floor((now.getTime() - lastUpdated.getTime()) / 1000))
    : null

  // [SOURCE-HEALTH] sources that went quiet — last_seen 2+ days ago within
  // the 30-day health window. Ranked by volume: the bigger the source, the
  // bigger the capture impact of its silence. Computed entirely client-side
  // from the existing /api/top_groups payload (no new endpoint needed).
  const quietSources = useMemo<QuietSource[]>(() => {
    if (healthGroups.length === 0) return []
    const todayStart = new Date()
    todayStart.setHours(0, 0, 0, 0)
    return healthGroups
      .map((g) => {
        const last = new Date(`${g.last_seen}T00:00:00`)
        const days = Number.isFinite(last.getTime())
          ? Math.round((todayStart.getTime() - last.getTime()) / 86_400_000)
          : 0
        return { ...g, daysQuiet: Math.max(0, days) }
      })
      .filter((g) => g.daysQuiet >= QUIET_AFTER_DAYS)
      .sort((a, b) => b.total - a.total)
  }, [healthGroups])
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white">
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-emerald-500/10 rounded-full blur-3xl" />
        <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-blue-500/10 rounded-full blur-3xl" />
      </div>

      <div className="relative z-10 container mx-auto px-4 py-8 max-w-6xl">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-8"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-emerald-500 to-blue-500 flex items-center justify-center shadow-lg shadow-emerald-500/20">
                <Link2 className="w-6 h-6 text-white" />
              </div>
              <div>
                <h1 className="text-2xl md:text-3xl font-bold bg-gradient-to-r from-emerald-400 via-white to-blue-400 bg-clip-text text-transparent">
                  مراقب الروابط
                </h1>
                <p className="text-slate-400 text-xs">نظام سحب الروابط الذكي</p>
              </div>
            </div>
            <div className="flex items-center gap-2 md:gap-3">
              {/* [LIVE-STATUS] Connection pill — the incident lesson: the
                  dashboard failed silently for 2 days. Now the header always
                  shows whether the API is reachable + how fresh the data is. */}
              <div
                className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium backdrop-blur-sm ${
                  apiError
                    ? 'border-rose-500/40 bg-rose-500/10 text-rose-300'
                    : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                }`}
                title={apiError || 'الاتصال بالخادم يعمل بشكل طبيعي'}
              >
                <span className="relative flex w-2 h-2">
                  <span
                    className={`absolute inline-flex w-full h-full rounded-full opacity-75 animate-ping ${
                      apiError ? 'bg-rose-400' : 'bg-emerald-400'
                    }`}
                  />
                  <span
                    className={`relative inline-flex w-2 h-2 rounded-full ${
                      apiError ? 'bg-rose-500' : 'bg-emerald-500'
                    }`}
                  />
                </span>
                <span className="hidden sm:inline">
                  {apiError ? 'انقطع الاتصال' : 'متصل'}
                </span>
                {!apiError && secondsAgo !== null && (
                  <span className="text-emerald-400/70 font-mono text-[10px]">
                    · {secondsAgo <= 0 ? 'الآن' : `قبل ${secondsAgo}ث`}
                  </span>
                )}
              </div>
              {/* [FLEET-VIEW] readiness micro-badges (ungated /ready data) */}
              {readiness && (
                <div className="hidden md:flex items-center gap-1.5">
                  <span
                    className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium border ${
                      readiness.bot_connected
                        ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                        : 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                    }`}
                    title="اتصال بوت تيليجرام"
                  >
                    بوت {readiness.bot_connected ? '✓' : '✗'}
                  </span>
                  <span
                    className={`flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium border ${
                      readiness.db_connected
                        ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                        : 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                    }`}
                    title="اتصال قاعدة البيانات"
                  >
                    قاعدة {readiness.db_connected ? '✓' : '✗'}
                  </span>
                </div>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={refreshAll}
                className="text-slate-400 hover:text-white"
                aria-label="تحديث البيانات"
              >
                <RefreshCw className="w-4 h-4" />
              </Button>
            </div>
          </div>
        </motion.div>

        {/* [DASHBOARD-RESTORE] Visible error banner — replaces the old silent
            failure mode where every card was just empty with no clue why. */}
        {apiError && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-6"
          >
            <div className="flex items-start gap-3 p-4 rounded-xl border border-rose-500/40 bg-rose-950/40 backdrop-blur-sm">
              <AlertTriangle className="w-5 h-5 text-rose-400 mt-0.5 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-bold text-rose-200 mb-1">
                  خطأ في الاتصال بالخادم
                </p>
                <p className="text-xs text-rose-300/90 leading-relaxed">
                  {apiError}
                </p>
                <p className="text-[10px] text-rose-400/70 mt-2 font-mono">
                  API: {API_URL} · مرّبع الطلب كل 15 ثانية تلقائيًا
                </p>
              </div>
              <button
                onClick={refreshAll}
                className="text-rose-300 hover:text-white transition-colors flex-shrink-0"
                aria-label="إعادة المحاولة"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>
          </motion.div>
        )}

        {/* Main Stats Cards — [STYLING] skeleton tiles during the very
            first fetch (no zero-flash); animate in once data lands. */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4 mb-6"
        >
          {!stats && !apiError ? (
            [0, 1, 2, 3].map((i) => (
              <Card key={i} className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm">
                <CardContent className="p-4">
                  <Skeleton className="h-3 w-1/2 mb-2.5" />
                  <Skeleton className="h-7 w-2/3" />
                </CardContent>
              </Card>
            ))
          ) : (
            <>
              <StatCard
                icon={<Link2 className="w-5 h-5" />}
                label="إجمالي الروابط"
                value={stats?.total_links ?? 0}
                gradient="from-emerald-500/20 to-emerald-500/5"
                iconColor="text-emerald-400"
                onClick={() => setModal('all_links')}
              />
              <StatCard
                icon={<MessageCircle className="w-5 h-5" />}
                label="🟢 واتساب"
                value={stats?.whatsapp_links ?? 0}
                gradient="from-green-500/20 to-green-500/5"
                iconColor="text-green-400"
                onClick={() => setModal('whatsapp')}
              />
              <StatCard
                icon={<Send className="w-5 h-5" />}
                label="🔵 تيليجرام"
                value={stats?.telegram_links ?? 0}
                gradient="from-blue-500/20 to-blue-500/5"
                iconColor="text-blue-400"
                onClick={() => setModal('telegram')}
              />
              <StatCard
                icon={<Users className="w-5 h-5" />}
                label="المراقبون"
                value={stats?.active_watchers ?? 0}
                gradient="from-purple-500/20 to-purple-500/5"
                iconColor="text-purple-400"
                onClick={() => setModal('joiners')}
              />
            </>
          )}
        </motion.div>

        {/* [TREND-VIEW] Daily capture trend — stacked bars (Telegram bottom /
            WhatsApp top) from /api/links_daily. Hover a bar for the exact
            day breakdown; dashed line = window average.
            [WINDOW] 7/14/30 selector — also drives the hourly strip below
            and the top-groups card (all views share one window). */}
        {trendDaily.length > 0 && (
          <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center justify-between text-lg">
                <span className="flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-emerald-400" />
                  اتجاه الالتقاط اليومي
                  <span className="text-xs text-slate-500 font-normal">(آخر {trendDays} يوم)</span>
                </span>
                <div className="flex items-center gap-2 flex-wrap">
                  {trendTotals?.best_day && (
                    <Badge className="bg-emerald-500/20 text-emerald-300 border-emerald-500/40 text-[10px]">
                      🏆 أعلى يوم: {trendTotals.best_day.count.toLocaleString()}
                    </Badge>
                  )}
                  {trendTotals && (
                    <Badge className="bg-slate-700/50 text-slate-300 border-slate-600/40 text-[10px]">
                      ⌀ {trendTotals.avg_per_day.toLocaleString()}/يوم
                    </Badge>
                  )}
                  {/* [WINDOW] 7/14/30-day selector (shared window) */}
                  <div
                    className="flex items-center gap-0.5 bg-slate-900/70 border border-slate-700/50 rounded-lg p-0.5"
                    role="tablist"
                    aria-label="النافذة الزمنية"
                  >
                    {[7, 14, 30].map((d) => (
                      <button
                        key={d}
                        role="tab"
                        aria-selected={trendDays === d}
                        onClick={() => setTrendDays(d)}
                        className={`px-2 py-0.5 rounded-md text-[11px] font-medium transition-colors ${
                          trendDays === d
                            ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                            : 'text-slate-400 hover:text-slate-200 border border-transparent'
                        }`}
                      >
                        {d}ي
                      </button>
                    ))}
                  </div>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <TrendChart daily={trendDaily} totals={trendTotals} />
              <div className="flex items-center justify-center gap-4 mt-1 text-[10px] text-slate-400">
                <span className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-sm bg-gradient-to-b from-emerald-400 to-emerald-600" />
                  واتساب
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-sm bg-gradient-to-b from-blue-400 to-blue-600" />
                  تيليجرام
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-4 border-t border-dashed border-slate-400" />
                  المتوسط
                </span>
              </div>
              {/* [HEATMAP-VIEW] hour-of-day activity strip — WHEN do links
                  get posted? Peak hour highlighted in amber. */}
              {hourly.length > 0 && <HourlyStrip hourly={hourly} />}
            </CardContent>
          </Card>
        )}

        {/* [SOURCE-VIEW] Top link-producing groups — link-source attribution
            over the same window as the trend chart. Split bars show the
            WA/TG mix; hover a row for the full breakdown + last activity. */}
        {topGroups.length > 0 && (
          <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center justify-between text-lg">
                <span className="flex items-center gap-2">
                  <BarChart3 className="w-5 h-5 text-fuchsia-400" />
                  أعلى المصادر إنتاجاً للروابط
                  <span className="text-xs text-slate-500 font-normal">
                    (آخر {trendDays} يوم · {topGroupsTotals?.total.toLocaleString() || '—'} رابط)
                  </span>
                </span>
                {topGroupsTotals && topGroupsTotals.distinct_groups > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setModal('top_groups')}
                    className="text-slate-400 hover:text-white text-xs h-7"
                  >
                    عرض الكل ({topGroupsTotals.distinct_groups.toLocaleString()})
                    <ArrowRight className="w-3.5 h-3.5 mr-1 rotate-180" />
                  </Button>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {topGroups.slice(0, 6).map((g, i) => (
                  <TopGroupRow key={g.group} rank={i + 1} group={g} max={topGroups[0]?.total || 1} />
                ))}
              </div>
              <div className="flex items-center justify-center gap-4 mt-3 text-[10px] text-slate-400">
                <span className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-sm bg-gradient-to-r from-emerald-400 to-emerald-600" />
                  واتساب
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-sm bg-gradient-to-r from-blue-400 to-blue-600" />
                  تيليجرام
                </span>
              </div>
            </CardContent>
          </Card>
        )}

        {/* [SOURCE-HEALTH] Quiet sources — producing sources from the last
            30 days that went silent (2+ days without a single link).
            Surfaces capture-drop causes (a group that removed the watcher,
            went private, or dried up) BEFORE the daily totals visibly dip.
            Data: /api/top_groups?days=30 (client-side daysQuiet). */}
        {healthGroups.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35 }}
          >
            <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center justify-between text-lg">
                  <span className="flex items-center gap-2">
                    <VolumeX className="w-5 h-5 text-orange-400" />
                    مصادر هادئة
                    <span className="text-xs text-slate-500 font-normal">
                      (آخر {HEALTH_WINDOW_DAYS} يوم · أعلى 50 مصدراً)
                    </span>
                  </span>
                  {quietSources.length > 0 ? (
                    <Badge className="bg-rose-500/20 text-rose-300 border-rose-500/40">
                      🔇 {quietSources.length} مصدر
                    </Badge>
                  ) : (
                    <Badge className="bg-emerald-500/20 text-emerald-300 border-emerald-500/40">
                      ✅ نشطة
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {quietSources.length === 0 ? (
                  <div className="flex items-center gap-3 p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10">
                    <ShieldCheck className="w-5 h-5 text-emerald-400 flex-shrink-0" />
                    <p className="text-sm text-emerald-200">
                      كل المصادر النشطة أنتجت روابط خلال آخر يومين — لا يوجد توقف ملحوظ.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2.5">
                    {quietSources.slice(0, 5).map((g, i) => {
                      const stopped = g.daysQuiet >= STOPPED_AFTER_DAYS
                      // silence-age bar — fills up the 30-day track as the
                      // source stays quiet (amber = slowing, red = stopped)
                      const stalenessPct = Math.min(
                        100,
                        Math.max(4, Math.round((g.daysQuiet / HEALTH_WINDOW_DAYS) * 100))
                      )
                      return (
                        <motion.div
                          key={g.group}
                          initial={{ opacity: 0, x: 20 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: Math.min(i * 0.07, 0.35), duration: 0.3 }}
                          className="group bg-slate-900/50 rounded-lg p-3 border border-slate-700/50 hover:border-slate-600/60 transition-colors"
                          title={`نشاط ${g.first_seen} ← ${g.last_seen} · واتساب ${g.whatsapp.toLocaleString()} / تيليجرام ${g.telegram.toLocaleString()}`}
                        >
                          <div className="flex items-center justify-between gap-2 mb-2">
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="text-xs w-6 text-center flex-shrink-0" aria-hidden>
                                {stopped ? '⏹️' : '🐢'}
                              </span>
                              <span
                                className={`text-sm truncate ${g.group === 'غير محدد' ? 'text-slate-500 italic' : 'text-slate-200'}`}
                                title={g.group}
                              >
                                {g.group}
                              </span>
                            </div>
                            <div className="flex items-center gap-2 flex-shrink-0">
                              <span className="text-[10px] text-slate-500 tabular-nums whitespace-nowrap">
                                {g.total.toLocaleString()} رابط
                              </span>
                              <span
                                className={`text-[10px] px-2 py-0.5 rounded-full border font-medium whitespace-nowrap ${
                                  stopped
                                    ? 'border-rose-500/40 bg-rose-500/10 text-rose-300'
                                    : 'border-amber-500/40 bg-amber-500/10 text-amber-300'
                                }`}
                              >
                                {stopped ? 'متوقف' : 'تباطؤ'} · منذ {g.daysQuiet} يوم
                              </span>
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <div className="flex-1 flex h-1.5 rounded-full bg-slate-700/40 overflow-hidden">
                              <motion.div
                                initial={{ width: 0 }}
                                animate={{ width: `${stalenessPct}%` }}
                                transition={{ duration: 0.6, ease: 'easeOut', delay: Math.min(i * 0.07, 0.35) }}
                                className={`h-full ${
                                  stopped
                                    ? 'bg-gradient-to-r from-rose-500 to-red-600'
                                    : 'bg-gradient-to-r from-amber-400 to-orange-500'
                                }`}
                              />
                            </div>
                            <span className="text-[9px] text-slate-600">30ي</span>
                          </div>
                          {/* hover detail line — same pattern as TopGroupRow */}
                          <div className="text-[10px] text-slate-500 mt-1.5 h-4 opacity-0 group-hover:opacity-100 transition-opacity">
                            <span className="text-emerald-500">واتساب {g.whatsapp.toLocaleString()}</span>
                            {' · '}
                            <span className="text-blue-400">تيليجرام {g.telegram.toLocaleString()}</span>
                            {g.other > 0 && <span> · أخرى {g.other.toLocaleString()}</span>}
                            <span> · نشاط {g.first_seen} ← {g.last_seen}</span>
                          </div>
                        </motion.div>
                      )
                    })}
                    {quietSources.length > 5 && (
                      <p className="text-[10px] text-slate-500 text-center">
                        + {quietSources.length - 5} مصادر هادئة أخرى (المعروض: الأعلى حجماً)
                      </p>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </motion.div>
        )}

        {/* [FLEET-VIEW] Joiner Fleet Health — from ungated /ready. Shows at a
            glance whether joins are actually being processed: connected /
            floodwait / disconnected / safety-guard-blocked joiners + a red
            alert when ALL joiners are unavailable. */}
        {readiness?.fleet_health && (
          <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center justify-between text-lg">
                <span className="flex items-center gap-2">
                  <Activity className="w-5 h-5 text-cyan-400" />
                  صحة أسطول الانضمام
                </span>
                {readiness.fleet_health.all_joiners_unavailable ? (
                  <Badge className="bg-rose-500/20 text-rose-300 border-rose-500/40 animate-pulse">
                    ⛔ كل الحسابات غير متاحة
                  </Badge>
                ) : readiness.fleet_health.floodwait_joiners_count > 0 ? (
                  <Badge className="bg-amber-500/20 text-amber-300 border-amber-500/40">
                    ⏳ FloodWait × {readiness.fleet_health.floodwait_joiners_count}
                  </Badge>
                ) : (
                  <Badge className="bg-emerald-500/20 text-emerald-300 border-emerald-500/40">
                    ✅ يعمل بشكل طبيعي
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
                  <CheckCircle2 className="w-5 h-5 text-emerald-400 mx-auto mb-1" />
                  <p className="text-2xl font-bold text-emerald-400">
                    {readiness.fleet_health.connected_joiners}
                  </p>
                  <p className="text-xs text-slate-400 mt-1">متصل وجاهز</p>
                </div>
                <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center">
                  <Clock3 className="w-5 h-5 text-amber-400 mx-auto mb-1" />
                  <p className="text-2xl font-bold text-amber-400">
                    {readiness.fleet_health.floodwait_joiners_count}
                  </p>
                  <p className="text-xs text-slate-400 mt-1">FloodWait مؤقت</p>
                </div>
                <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center">
                  <XCircle className="w-5 h-5 text-red-400 mx-auto mb-1" />
                  <p className="text-2xl font-bold text-red-400">
                    {readiness.fleet_health.disconnected_joiners_count}
                  </p>
                  <p className="text-xs text-slate-400 mt-1">غير متصل</p>
                </div>
                <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-3 text-center">
                  <AlertTriangle className="w-5 h-5 text-purple-400 mx-auto mb-1" />
                  <p className="text-2xl font-bold text-purple-400">
                    {readiness.fleet_health.safety_guard_blocked_joiners}
                  </p>
                  <p className="text-xs text-slate-400 mt-1">حظر وقائي</p>
                </div>
              </div>
              {readiness.scan_running && (
                <p className="text-xs text-cyan-400/80 mt-3 flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
                  فحص المحفوظات التاريخية يعمل الآن...
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {/* [PENDING-VIEW] Groups awaiting admin approval — REQAUDIT-2
            lifecycle. The 30-min self-healing recheck loop flips these to
            JOINED automatically once an admin approves the join request. */}
        {pendingSummary && pendingSummary.total_pending_approval > 0 && (
          <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center justify-between text-lg">
                <span className="flex items-center gap-2">
                  <Clock3 className="w-5 h-5 text-sky-400" />
                  بانتظار موافقة المشرف
                </span>
                <div className="flex items-center gap-2">
                  {pendingSummary.self_healing && (
                    <Badge className="bg-sky-500/20 text-sky-300 border-sky-500/40 text-[10px]">
                      🔄 فحص تلقائي كل {Math.round(pendingSummary.recheck_interval_seconds / 60)} دقيقة
                    </Badge>
                  )}
                  <Badge className="bg-sky-500/20 text-sky-300 border-sky-500/40">
                    {pendingSummary.total_pending_approval} مجموعة
                  </Badge>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <button
                onClick={() => setModal('pending_approvals')}
                className="w-full text-right hover:scale-[1.01] transition-transform"
              >
                <div className="bg-slate-700/30 rounded-lg p-3 border border-slate-700">
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-sm font-semibold text-slate-300">
                      ✉️ طلبات انضمام مُعلّقة (تُشفى تلقائيًا عند القبول)
                    </h4>
                    <ArrowRight className="w-4 h-4 text-slate-400" />
                  </div>
                  <div className="space-y-1">
                    {pendingApprovals.slice(0, 4).map((p) => (
                      <div
                        key={p.id}
                        className="bg-slate-900/50 rounded p-2 text-xs flex items-center justify-between"
                      >
                        <span className="text-white truncate flex-1 font-mono" dir="ltr">
                          {p.group_link || '؟'}
                        </span>
                        <span className="text-slate-500 mr-2 font-mono text-[10px]">
                          {p.joined_by_phone}
                        </span>
                      </div>
                    ))}
                    {pendingApprovals.length > 4 && (
                      <p className="text-xs text-slate-500 text-center pt-1">
                        + {pendingApprovals.length - 4} طلب آخر...
                      </p>
                    )}
                  </div>
                </div>
              </button>
            </CardContent>
          </Card>
        )}

        {/* AI Stats Section */}
        {stats?.ai_stats && (
          <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
            <CardHeader>
              <CardTitle className="flex items-center justify-between text-lg">
                <span className="flex items-center gap-2">
                  <span className="text-2xl">🤖</span>
                  تحليل الذكاء الاصطناعي
                </span>
                <Badge
                  variant="outline"
                  className={
                    stats.ai_stats.ai_batch_mode
                      ? 'border-amber-500/40 text-amber-400 bg-amber-500/10'
                      : 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
                  }
                >
                  {stats.ai_stats.ai_batch_mode ? '⏭️ Batch Mode' : '🤖 AI Active'}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <button
                  onClick={() => setModal('ai_approved')}
                  className="text-right hover:scale-105 transition-transform"
                >
                  <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
                    <CheckCircle2 className="w-5 h-5 text-emerald-400 mx-auto mb-1" />
                    <p className="text-2xl font-bold text-emerald-400">
                      {stats.ai_stats.ai_approved}
                    </p>
                    <p className="text-xs text-slate-400 mt-1">✅ موافق عليه</p>
                  </div>
                </button>
                <button
                  onClick={() => setModal('ai_rejected')}
                  className="text-right hover:scale-105 transition-transform"
                >
                  <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center">
                    <XCircle className="w-5 h-5 text-red-400 mx-auto mb-1" />
                    <p className="text-2xl font-bold text-red-400">
                      {stats.ai_stats.ai_rejected}
                    </p>
                    <p className="text-xs text-slate-400 mt-1">❌ مرفوض</p>
                  </div>
                </button>
                <button
                  onClick={() => setModal('ai_ads')}
                  className="text-right hover:scale-105 transition-transform"
                >
                  <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center">
                    <AlertTriangle className="w-5 h-5 text-amber-400 mx-auto mb-1" />
                    <p className="text-2xl font-bold text-amber-400">
                      {stats.ai_stats.ai_ads}
                    </p>
                    <p className="text-xs text-slate-400 mt-1">⚠️ إعلان</p>
                  </div>
                </button>
                <div className="bg-slate-700/30 border border-slate-700 rounded-lg p-3 text-center">
                  <Clock3 className="w-5 h-5 text-slate-300 mx-auto mb-1" />
                  <p className="text-2xl font-bold text-slate-300">
                    {stats.ai_stats.ai_pending}
                  </p>
                  <p className="text-xs text-slate-400 mt-1">⏳ لم يُفحص</p>
                </div>
              </div>
              {/* [AI-PROGRESS] classification backlog at a glance — how much
                  of the captured corpus the AI classifier has processed.
                  With a 26K+ backlog the four raw counters above don't convey
                  the scale; the progress bar makes it obvious at a glance. */}
              {(() => {
                const processed =
                  stats.ai_stats.ai_approved +
                  stats.ai_stats.ai_rejected +
                  stats.ai_stats.ai_ads
                const totalAi = processed + stats.ai_stats.ai_pending
                const pct = totalAi > 0 ? (processed / totalAi) * 100 : 0
                return (
                  <div className="mt-3">
                    <div className="flex items-center justify-between text-[10px] text-slate-400 mb-1.5">
                      <span>
                        تقدّم الفحص:{' '}
                        <span className="text-emerald-300 font-semibold">
                          {processed.toLocaleString()}
                        </span>{' '}
                        من {totalAi.toLocaleString()} رابط
                      </span>
                      <span className="tabular-nums">{pct.toFixed(1)}%</span>
                    </div>
                    <div
                      className="h-2.5 rounded-full bg-slate-700/50 overflow-hidden border border-slate-700/40"
                      role="progressbar"
                      aria-label="تقدّم فحص الذكاء الاصطناعي"
                      aria-valuenow={Math.round(pct)}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      {/* min 0.8% width so a tiny ratio is still visible */}
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${Math.max(pct, 0.8)}%` }}
                        transition={{ duration: 0.8, ease: 'easeOut' }}
                        className="h-full bg-gradient-to-r from-emerald-400 to-teal-500"
                      />
                    </div>
                    <p className="text-[10px] text-slate-500 mt-1.5">
                      {stats.ai_stats.ai_pending > 0 ? (
                        <>
                          متبقي{' '}
                          <span className="text-amber-300 font-semibold">
                            {stats.ai_stats.ai_pending.toLocaleString()}
                          </span>{' '}
                          رابط بانتظار الفحص
                          {stats.ai_stats.ai_batch_mode
                            ? ' · وضع الدفعات: الفحص مؤجل'
                            : ''}
                        </>
                      ) : (
                        'اكتمل فحص جميع الروابط ✅'
                      )}
                    </p>
                  </div>
                )
              })()}
            </CardContent>
          </Card>
        )}
        {/* Monitored Chats Section — المجموعات المراقبة (بطاقة واحدة) */}
        <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
          <CardHeader>
            <CardTitle className="flex items-center justify-between text-lg">
              <span className="flex items-center gap-2">
                <span className="text-2xl">👁️</span>
                المجموعات المراقبة
              </span>
              <div className="flex items-center gap-2">
                {monitoredSummary && monitoredSummary.classified > 0 && (
                  <Badge className="bg-emerald-500/20 text-emerald-400 border-emerald-500/40 text-xs">
                    {monitoredSummary.classified} مُصنّفة
                  </Badge>
                )}
                {monitoredSummary && (
                  <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/40">
                    {monitoredSummary.total} مجموعة
                  </Badge>
                )}
              </div>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <button
              onClick={() => setModal('monitored_chats')}
              className="w-full text-right hover:scale-[1.01] transition-transform"
            >
              <div className="bg-slate-700/30 rounded-lg p-3 border border-slate-700">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-sm font-semibold text-slate-300">
                    📋 أحدث المجموعات المراقبة (تصنيف AI)
                  </h4>
                  <ArrowRight className="w-4 h-4 text-slate-400" />
                </div>
                {monitoredChats.length > 0 ? (
                  <div className="space-y-1">
                    {monitoredChats.slice(0, 5).map((c) => (
                      <div
                        key={c.chat_id}
                        className="bg-slate-900/50 rounded p-2 text-xs flex items-center justify-between"
                      >
                        <span className="text-white truncate flex-1">
                          {c.chat_title || 'غير معروف'}
                        </span>
                        <div className="flex items-center gap-2 mr-2">
                          {c.ai_classification && c.ai_classification !== 'unknown' && c.ai_classification !== 'error' && (
                            <Badge variant="outline" className="text-[10px] px-1 py-0">
                              {c.ai_classification === 'group' ? '👥' : c.ai_classification === 'channel' ? '📢' : '?'}
                            </Badge>
                          )}
                          {c.ai_relevance > 0 && (
                            <span className={`text-[10px] ${
                              c.ai_relevance >= 80 ? 'text-emerald-400' :
                              c.ai_relevance >= 50 ? 'text-amber-400' : 'text-red-400'
                            }`}>
                              {c.ai_relevance}%
                            </span>
                          )}
                          {c.ai_country && c.ai_country !== 'أخرى' && (
                            <span className="text-purple-400 text-[10px]">{c.ai_country}</span>
                          )}
                        </div>
                      </div>
                    ))}
                    {monitoredChats.length > 5 && (
                      <p className="text-xs text-slate-500 text-center pt-1">
                        + {monitoredChats.length - 5} مجموعة أخرى...
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500 text-center py-2">
                    لا توجد مجموعات مراقبة بعد
                  </p>
                )}
              </div>
            </button>
          </CardContent>
        </Card>

        {/* Joiner Dashboard Section */}
        <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
          <CardHeader>
            <CardTitle className="flex items-center justify-between text-lg">
              <span className="flex items-center gap-2">
                <span className="text-2xl">🚀</span>
                لوحة الفدائي
              </span>
              {joinersSummary && (
                <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/40">
                  {joinersSummary.connected_joiners}/{joinersSummary.total_joiners} متصل
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
                <p className="text-2xl font-bold text-emerald-400">
                  {joinersSummary?.total_joined_groups ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-1">مجموعة منضم إليها</p>
              </div>
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center">
                <p className="text-2xl font-bold text-amber-400">
                  {joinersSummary?.total_already_member ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-1">منضم مسبقاً</p>
              </div>
              <button
                onClick={() => setModal('banned_groups')}
                className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center hover:scale-105 transition-transform w-full"
              >
                <p className="text-2xl font-bold text-red-400">
                  {joinersSummary?.total_banned ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-1">مجموعات ممنوعة (اضغط للتفاصيل)</p>
              </button>
            </div>

            {joiners.length > 0 ? (
              <div>
                <h4 className="text-sm font-semibold text-slate-300 mb-2">
                  حسابات الفدائيين:
                </h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {joiners.map((j) => (
                    <div
                      key={j.phone}
                      className="bg-slate-700/30 rounded-lg p-3 border border-slate-700"
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-white font-mono text-xs">{j.phone}</span>
                        <span
                          className={`text-xs px-2 py-0.5 rounded ${
                            j.connected
                              ? 'bg-emerald-500/20 text-emerald-400'
                              : 'bg-red-500/20 text-red-400'
                          }`}
                        >
                          {j.connected ? '✅ متصل' : '❌ غير متصل'}
                        </span>
                      </div>
                      <div className="text-xs text-slate-400 flex justify-between">
                        <span>
                          انضمامات اليوم:{' '}
                          <span className="text-blue-400">
                            {j.daily_joins}/{j.daily_limit}
                          </span>
                        </span>
                        {j.last_join_timestamp && (
                          <span>
                            آخر:{' '}
                            {new Date(j.last_join_timestamp).toLocaleString('ar-SA', {
                              hour: '2-digit',
                              minute: '2-digit',
                              day: 'numeric',
                              month: 'short',
                            })}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>

                {/* المجموعات المنضم لها */}
                <div className="mt-4">
                  <button
                    onClick={() => setModal('joined_groups')}
                    className="w-full text-right hover:scale-[1.01] transition-transform"
                  >
                    <div className="bg-slate-700/30 rounded-lg p-3 border border-slate-700">
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="text-sm font-semibold text-slate-300">
                          📋 المجموعات المنضم لها ({joinedGroups.length})
                        </h4>
                        <ArrowRight className="w-4 h-4 text-slate-400" />
                      </div>
                      {joinedGroups.length > 0 ? (
                        <div className="space-y-1">
                          {joinedGroups.slice(0, 3).map((g, i) => (
                            <div
                              key={i}
                              className="bg-slate-900/50 rounded p-2 text-xs flex items-center justify-between"
                            >
                              <span className="text-white truncate flex-1">
                                {g.group_title || 'غير معروف'}
                              </span>
                              <div className="flex items-center gap-2 mr-2">
                                <span className="text-slate-400">
                                  {g.member_count > 0 ? `${g.member_count.toLocaleString()} عضو` : ''}
                                </span>
                                <span className="text-slate-500 font-mono">
                                  {g.joined_by_phone || '?'}
                                </span>
                              </div>
                            </div>
                          ))}
                          {joinedGroups.length > 3 && (
                            <p className="text-xs text-slate-500 text-center pt-1">
                              + {joinedGroups.length - 3} مجموعة أخرى...
                            </p>
                          )}
                        </div>
                      ) : (
                        <p className="text-xs text-slate-500 text-center py-2">
                          لا توجد مجموعات منضم لها بعد — البوت ينتظر مجموعات 1000+ عضو
                        </p>
                      )}
                    </div>
                  </button>
                </div>
              </div>
            ) : (
              <div className="text-center py-8">
                <p className="text-slate-500 text-sm">جارٍ تحميل بيانات الفدائيين...</p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Recent Links Preview */}
        <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="flex items-center justify-between text-lg">
              <span className="flex items-center gap-2">
                <Activity className="w-5 h-5 text-emerald-400" />
                أحدث الروابط
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setModal('all_links')}
                className="text-slate-400 hover:text-white text-xs"
              >
                عرض الكل <ArrowRight className="w-3 h-3 mr-1" />
              </Button>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-20 w-full bg-slate-800/50 rounded-xl" />
                ))}
              </div>
            ) : allLinks.length === 0 ? (
              <div className="text-center py-8">
                <Link2 className="w-10 h-10 mx-auto text-slate-600 mb-2" />
                <p className="text-slate-500 text-sm">لا توجد روابط</p>
              </div>
            ) : (
              <div className="space-y-2">
                {allLinks.slice(0, 5).map((link) => (
                  <LinkCard
                    key={link.id}
                    link={link}
                    compact
                    isMonitored={monitoredChats.some(
                      (c) =>
                        c.chat_title === link.group_name ||
                        (link.message_link &&
                          link.message_link.includes(
                            `/c/${String(c.chat_id).replace('-100', '')}/`
                          ))
                    )}
                  />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <footer className="mt-10 text-center text-slate-500 text-xs pb-6">
          <p>نظام مراقبة الروابط © 2026</p>
          <p className="mt-1 flex items-center justify-center gap-1.5">
            <span className="w-1 h-1 rounded-full bg-slate-600 animate-pulse" />
            تحديث تلقائي كل 15 ثانية
            {lastUpdated && (
              <span className="text-slate-600">
                · آخر تحديث{' '}
                {lastUpdated.toLocaleTimeString('ar-SA', {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                })}
              </span>
            )}
          </p>
        </footer>
      </div>

      {/* Modal */}
      <LinksModal
        type={modal}
        onClose={() => setModal(null)}
        allLinks={allLinks}
        joiners={joiners}
        joinersSummary={joinersSummary}
        joinedGroups={joinedGroups}
        monitoredChats={monitoredChats}
        monitoredSummary={monitoredSummary}
        bannedGroups={bannedGroups}
        pendingApprovals={pendingApprovals}
        pendingSummary={pendingSummary}
        topGroups={topGroups}
        topGroupsTotals={topGroupsTotals}
        trendDays={trendDays}
      />
    </div>
  )
}

// ===== useCountUp Hook — [STYLING] animated count-up for StatCard =====
function useCountUp(target: number, duration = 700): number {
  const [val, setVal] = useState(0)
  const prev = useRef(0)
  useEffect(() => {
    const from = prev.current
    if (from === target) {
      setVal(target)
      return
    }
    let raf = 0
    const t0 = performance.now()
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / duration)
      const eased = 1 - Math.pow(1 - p, 3) // easeOutCubic
      setVal(Math.round(from + (target - from) * eased))
      if (p < 1) raf = requestAnimationFrame(step)
      else prev.current = target
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])
  return val
}

// ===== timeAgo — [UX] relative timestamps ("منذ 5 د") =====
function timeAgo(iso: string, nowDate: Date = new Date()): string {
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const m = Math.floor((nowDate.getTime() - t) / 60000)
  if (m < 1) return 'الآن'
  if (m < 60) return `منذ ${m} د`
  const h = Math.floor(m / 60)
  if (h < 24) return `منذ ${h} س`
  const d = Math.floor(h / 24)
  if (d < 7) return `منذ ${d} يوم`
  return new Date(iso).toLocaleDateString('ar-SA', { day: 'numeric', month: 'short' })
}

// ===== exportCsv — [CSV-VIEW] client-side CSV export (BOM for Excel/Arabic) =====
function exportCsv(links: LinkItem[], filename: string) {
  const head = [
    'id', 'link', 'type', 'group_name', 'sender_name', 'created_at',
    'ai_approved', 'ai_is_ad', 'ai_country', 'ai_description',
  ]
  const esc = (v: unknown): string => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const rows = links.map((l) =>
    [
      l.id, l.link, l.link_type, l.group_name, l.sender_name, l.created_at,
      l.ai_approved === null || l.ai_approved === undefined ? '' : l.ai_approved,
      l.ai_is_ad === null || l.ai_is_ad === undefined ? '' : l.ai_is_ad,
      l.ai_country, l.ai_description,
    ].map(esc).join(',')
  )
  // \uFEFF BOM so Excel opens Arabic text as UTF-8 instead of mojibake.
  const csv = '\uFEFF' + [head.join(','), ...rows].join('\r\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// ===== CopyButton — [UX] one-click copy with visual feedback =====
function CopyButton(props: { text: string }) {
  const { text } = props
  const [copied, setCopied] = useState(false)
  const onCopy = async (e: ReactMouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard API unavailable (http/non-secure) — silent no-op
    }
  }
  return (
    <button
      onClick={onCopy}
      className={`transition-colors ${copied ? 'text-emerald-400' : 'text-slate-500 hover:text-white'}`}
      title={copied ? 'تم النسخ ✓' : 'نسخ الرابط'}
      aria-label={copied ? 'تم النسخ' : 'نسخ الرابط'}
    >
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  )
}

// ===== TrendChart — [TREND-VIEW] SVG stacked-bars daily capture chart =====
function TrendChart(props: { daily: DailyStat[]; totals: TrendTotals | null }) {
  const { daily, totals } = props
  const [hovered, setHovered] = useState<number | null>(null)
  if (daily.length === 0) return null

  const W = 720
  const H = 190
  const padL = 6
  const padR = 6
  const padT = 16
  const padB = 24
  const chartW = W - padL - padR
  const chartH = H - padT - padB
  const max = Math.max(...daily.map((d) => d.total), 1)
  const barW = chartW / daily.length
  const avg = totals?.avg_per_day ?? 0
  const avgY = padT + chartH - (avg / max) * chartH
  const hoverStat = hovered !== null ? daily[hovered] : null

  return (
    <div className="relative" dir="ltr">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full select-none"
        role="img"
        aria-label="مخطط الروابط اليومية"
      >
        <defs>
          <linearGradient id="tgGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#60a5fa" />
            <stop offset="100%" stopColor="#2563eb" />
          </linearGradient>
          <linearGradient id="waGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#34d399" />
            <stop offset="100%" stopColor="#059669" />
          </linearGradient>
        </defs>

        {/* horizontal gridlines (4) */}
        {[0.25, 0.5, 0.75, 1].map((f) => {
          const y = padT + chartH - f * chartH
          return (
            <line
              key={f}
              x1={padL}
              x2={W - padR}
              y1={y}
              y2={y}
              stroke="#334155"
              strokeWidth="0.5"
              opacity="0.35"
            />
          )
        })}

        {/* dashed average line */}
        {avg > 0 && (
          <line
            x1={padL}
            x2={W - padR}
            y1={avgY}
            y2={avgY}
            stroke="#94a3b8"
            strokeWidth="1"
            strokeDasharray="5 4"
            opacity="0.7"
          />
        )}

        {daily.map((d, i) => {
          const x = padL + i * barW + barW * 0.16
          const bw = barW * 0.68
          const tgH = (d.telegram / max) * chartH
          const waH = (d.whatsapp / max) * chartH
          const yBase = padT + chartH
          const isHover = hovered === i
          const isBest =
            totals?.best_day && d.date === totals.best_day.date && d.total > 0
          return (
            <g
              key={d.date}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            >
              {/* hover hit-area */}
              <rect
                x={padL + i * barW}
                y={padT}
                width={barW}
                height={chartH}
                fill={isHover ? 'rgba(255,255,255,0.05)' : 'transparent'}
              />
              {/* telegram (bottom, blue) */}
              <rect
                x={x}
                y={yBase - tgH}
                width={bw}
                height={Math.max(tgH, d.telegram > 0 ? 2 : 0)}
                rx="2"
                fill="url(#tgGrad)"
                opacity={isHover ? 1 : 0.8}
                className="transition-opacity"
              />
              {/* whatsapp (top, green) */}
              <rect
                x={x}
                y={yBase - tgH - waH}
                width={bw}
                height={Math.max(waH, d.whatsapp > 0 ? 2 : 0)}
                rx="2"
                fill="url(#waGrad)"
                opacity={isHover ? 1 : 0.85}
                className="transition-opacity"
              />
              {/* best-day crown marker */}
              {isBest && (
                <text
                  x={padL + i * barW + barW / 2}
                  y={padT - 4}
                  textAnchor="middle"
                  fontSize="10"
                >
                  👑
                </text>
              )}
              {/* x labels: first / middle / last */}
              {(i === 0 || i === daily.length - 1 || i === Math.floor(daily.length / 2)) && (
                <text
                  x={padL + i * barW + barW / 2}
                  y={H - 8}
                  textAnchor="middle"
                  fontSize="10"
                  fill="#64748b"
                >
                  {d.date.slice(5).replace('-', '/')}
                </text>
              )}
            </g>
          )
        })}
      </svg>

      {/* floating tooltip near the hovered bar */}
      {hoverStat && hovered !== null && (
        <div
          className="absolute -top-1 pointer-events-none z-10 bg-slate-900/95 border border-slate-600 rounded-lg px-3 py-2 text-xs shadow-xl backdrop-blur-sm whitespace-nowrap"
          style={{
            left: `${((hovered + 0.5) / daily.length) * 100}%`,
            transform: 'translateX(-50%)',
          }}
          dir="rtl"
        >
          <p className="font-bold text-white mb-1" dir="ltr">
            {hoverStat.date}
          </p>
          <p className="text-slate-300">
            الإجمالي:{' '}
            <span className="font-bold text-white">{hoverStat.total.toLocaleString()}</span>
          </p>
          <p className="text-emerald-400">
            واتساب: {hoverStat.whatsapp.toLocaleString()}
          </p>
          <p className="text-blue-400">
            تيليجرام: {hoverStat.telegram.toLocaleString()}
          </p>
          {hoverStat.other > 0 && (
            <p className="text-slate-400">أخرى: {hoverStat.other.toLocaleString()}</p>
          )}
        </div>
      )}
    </div>
  )
}

// ===== HourlyStrip — [HEATMAP-VIEW] hour-of-day activity (24 UTC bars) =====
// Answers "WHEN do links get posted?" over the selected window. Peak hour
// is highlighted amber; hover a bar for the exact WA/TG breakdown.
function HourlyStrip(props: { hourly: HourlyStat[] }) {
  const { hourly } = props
  const [hovered, setHovered] = useState<number | null>(null)
  if (hourly.length === 0) return null

  const W = 720
  const H = 88
  const padL = 6
  const padR = 6
  const padT = 14
  const padB = 18
  const chartW = W - padL - padR
  const chartH = H - padT - padB
  const max = Math.max(...hourly.map((h) => h.total), 1)
  const barW = chartW / hourly.length
  const peak = hourly.reduce((a, b) => (b.total > a.total ? b : a), hourly[0])
  const hoverStat = hovered !== null ? hourly[hovered] : null

  return (
    <div className="relative mt-4 pt-3 border-t border-slate-700/40" dir="ltr">
      <div className="flex items-center justify-between mb-1 px-1" dir="rtl">
        <span className="text-[10px] text-slate-500">
          النشاط حسب الساعة (UTC)
        </span>
        {peak && peak.total > 0 && (
          <span className="text-[10px] text-amber-300 flex items-center gap-1">
            <Flame className="w-3 h-3" />
            ذروة النشاط {String(peak.hour).padStart(2, '0')}:00
            <span className="text-amber-400/60 font-mono">
              ({peak.total.toLocaleString()})
            </span>
          </span>
        )}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full select-none"
        role="img"
        aria-label="مخطط النشاط بالساعة"
      >
        <defs>
          <linearGradient id="hourGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#818cf8" />
            <stop offset="100%" stopColor="#6366f1" />
          </linearGradient>
        </defs>
        {hourly.map((h, i) => {
          const x = padL + i * barW + barW * 0.2
          const bw = barW * 0.6
          const bh = (h.total / max) * chartH
          const yBase = padT + chartH
          const isPeak = peak && h.hour === peak.hour && h.total > 0
          const isHover = hovered === i
          return (
            <g
              key={h.hour}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            >
              <rect
                x={padL + i * barW}
                y={padT}
                width={barW}
                height={chartH}
                fill={isHover ? 'rgba(255,255,255,0.05)' : 'transparent'}
              />
              <rect
                x={x}
                y={yBase - bh}
                width={bw}
                height={Math.max(bh, h.total > 0 ? 2 : 0)}
                rx="1.5"
                fill={isPeak ? '#fbbf24' : 'url(#hourGrad)'}
                opacity={isHover ? 1 : isPeak ? 0.95 : 0.7}
              />
              {/* x labels every 6 hours */}
              {i % 6 === 0 && (
                <text
                  x={padL + i * barW + barW / 2}
                  y={H - 5}
                  textAnchor="middle"
                  fontSize="9"
                  fill="#64748b"
                >
                  {String(h.hour).padStart(2, '0')}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {hoverStat && hovered !== null && (
        <div
          className="absolute top-7 pointer-events-none z-10 bg-slate-900/95 border border-slate-600 rounded-lg px-3 py-2 text-xs shadow-xl backdrop-blur-sm whitespace-nowrap"
          style={{
            left: `${((hovered + 0.5) / hourly.length) * 100}%`,
            transform: 'translateX(-50%)',
          }}
          dir="rtl"
        >
          <p className="font-bold text-white mb-1" dir="ltr">
            {String(hoverStat.hour).padStart(2, '0')}:00
          </p>
          <p className="text-slate-300">
            الإجمالي:{' '}
            <span className="font-bold text-white">
              {hoverStat.total.toLocaleString()}
            </span>
          </p>
          {hoverStat.whatsapp > 0 && (
            <p className="text-emerald-400">واتساب: {hoverStat.whatsapp.toLocaleString()}</p>
          )}
          {hoverStat.telegram > 0 && (
            <p className="text-blue-400">تيليجرام: {hoverStat.telegram.toLocaleString()}</p>
          )}
        </div>
      )}
    </div>
  )
}

// ===== TopGroupRow — [SOURCE-VIEW] one ranked source row ================
// Split bar (green WA / blue TG) scaled to the #1 group; medals for the
// top 3; hover reveals the full breakdown + first/last activity dates.
function TopGroupRow(props: { rank: number; group: TopGroup; max: number }) {
  const { rank, group, max } = props
  const medals = ['🥇', '🥈', '🥉']
  const rankLabel = medals[rank - 1] || <span className="text-slate-500">#{rank}</span>
  const widthPct = Math.max(2, Math.round((group.total / max) * 100))
  const waPct = group.total
    ? Math.round((group.whatsapp / group.total) * widthPct)
    : 0
  const tgPct = widthPct - waPct
  const isUnnamed = group.group === 'غير محدد'

  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: Math.min(rank * 0.06, 0.4), duration: 0.35 }}
      className="group"
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs w-6 text-center flex-shrink-0" aria-hidden>
            {rankLabel}
          </span>
          <span
            className={`text-sm truncate ${isUnnamed ? 'text-slate-500 italic' : 'text-slate-200'}`}
            title={group.group}
          >
            {group.group}
          </span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-[10px] text-slate-500 tabular-nums">
            {group.share.toFixed(1)}%
          </span>
          <span className="text-sm font-bold text-white tabular-nums">
            {group.total.toLocaleString()}
          </span>
        </div>
      </div>
      <div className="flex h-2 rounded-full bg-slate-700/40 overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${waPct}%` }}
          transition={{ duration: 0.6, ease: 'easeOut', delay: Math.min(rank * 0.06, 0.4) }}
          className="h-full bg-gradient-to-r from-emerald-400 to-emerald-600"
        />
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${tgPct}%` }}
          transition={{ duration: 0.6, ease: 'easeOut', delay: Math.min(rank * 0.06, 0.4) + 0.08 }}
          className="h-full bg-gradient-to-r from-blue-400 to-blue-600"
        />
      </div>
      {/* hover detail line */}
      <div className="text-[10px] text-slate-500 mt-1 h-4 opacity-0 group-hover:opacity-100 transition-opacity">
        <span className="text-emerald-500">واتساب {group.whatsapp.toLocaleString()}</span>
        {' · '}
        <span className="text-blue-400">تيليجرام {group.telegram.toLocaleString()}</span>
        {group.other > 0 && <span> · أخرى {group.other.toLocaleString()}</span>}
        <span> · نشاط {group.first_seen} ← {group.last_seen}</span>
      </div>
    </motion.div>
  )
}

// ===== StatCard Component =====
function StatCard(props: {
  icon: ReactNode
  label: string
  value: number
  gradient: string
  iconColor: string
  onClick: () => void
}) {
  const { icon, label, value, gradient, iconColor, onClick } = props
  // [STYLING] animated count-up — numbers roll smoothly on every refresh
  const animated = useCountUp(value)
  return (
    <button
      onClick={onClick}
      className="text-right hover:scale-105 transition-transform w-full group"
    >
      <Card
        className={`bg-gradient-to-br ${gradient} border-slate-700/50 backdrop-blur-sm overflow-hidden relative`}
      >
        <CardContent className="p-4">
          {/* subtle hover sheen */}
          <div className="absolute inset-0 bg-gradient-to-t from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
          <div className="flex items-center justify-between">
            <div>
              <p className="text-slate-400 text-xs mb-1">{label}</p>
              <p className="text-2xl font-bold text-white">{animated.toLocaleString()}</p>
            </div>
            <div className={`${iconColor} opacity-80 group-hover:opacity-100 group-hover:scale-110 transition-all`}>{icon}</div>
          </div>
        </CardContent>
      </Card>
    </button>
  )
}

// ===== LinkCard Component =====
function LinkCard(props: { link: LinkItem; compact?: boolean; isMonitored?: boolean }) {
  const { link, compact = false, isMonitored = false } = props
  const isWhatsapp = link.link_type === 'whatsapp'
  const date = new Date(link.created_at)
  // [UX] full timestamp on hover; friendly relative label on display
  const fullTimeStr = date.toLocaleString('ar-SA', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
  const timeStr = timeAgo(link.created_at)
  const country =
    null
  const href = safeUrl(link.link)

  if (compact) {
    return (
      <div className="bg-slate-700/30 rounded-lg p-3 border border-slate-700/50 hover:bg-slate-700/50 transition-colors">
        <div className="flex items-center gap-2 mb-1">
          <div
            className={`w-2 h-2 rounded-full ${isWhatsapp ? 'bg-green-500' : 'bg-blue-500'}`}
          />
          <span className="text-xs text-slate-300 truncate flex-1">
            {link.group_name || 'غير معروف'}
            {isMonitored && (
              <span className="mr-1 text-emerald-400" title="المجموعة مصدر مراقَب">●</span>
            )}
          </span>
          <span className="text-xs text-slate-500" title={fullTimeStr}>{timeStr}</span>
          {href && <CopyButton text={href} />}
        </div>
        {href && (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-400 hover:text-blue-300 truncate block font-mono"
          >
            {href}
          </a>
        )}
      </div>
    )
  }

  return (
    <Card className="mb-3 bg-slate-800/30 border-slate-700/50 backdrop-blur-sm hover:bg-slate-800/50 transition-all duration-300 overflow-hidden">
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2 flex-wrap">
            <div
              className={`w-3 h-3 rounded-full ${
                isWhatsapp ? 'bg-green-500' : 'bg-blue-500'
              } shadow-lg`}
            />
            <Badge
              variant="outline"
              className={
                isWhatsapp
                  ? 'border-green-500/30 text-green-400'
                  : 'border-blue-500/30 text-blue-400'
              }
            >
              {isWhatsapp ? '🟢 واتساب' : '🔵 تيليجرام'}
            </Badge>
            {country && (
              <Badge variant="outline" className="border-purple-500/30 text-purple-400">
                {country}
              </Badge>
            )}
            {link.ai_approved === true && (
              <Badge
                variant="outline"
                className="border-emerald-500/40 text-emerald-400 bg-emerald-500/10"
              >
                ✅ AI موافق
              </Badge>
            )}
            {link.ai_approved === false && (
              <Badge
                variant="outline"
                className="border-red-500/40 text-red-400 bg-red-500/10"
              >
                ❌ AI مرفوض
              </Badge>
            )}
            {link.ai_is_ad === true && (
              <Badge
                variant="outline"
                className="border-amber-500/40 text-amber-400 bg-amber-500/10"
              >
                ⚠️ إعلان
              </Badge>
            )}
            {isMonitored ? (
              <Badge
                variant="outline"
                className="border-cyan-500/40 text-cyan-400 bg-cyan-500/10"
                title="المجموعة المصدر مسجّلة في قائمة المراقبة"
              >
                👁️ مصدر مراقَب
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="border-slate-600/40 text-slate-500 bg-slate-700/10"
                title="المجموعة المصدر غير مسجّلة في قائمة المراقبة"
              >
                ⚪ مصدر غير مراقَب
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1.5 text-slate-500 text-xs">
            <Clock className="w-3 h-3" />
            <span title={fullTimeStr}>{timeStr}</span>
            {href && <CopyButton text={href} />}
          </div>
        </div>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="block mb-3 group"
          >
            <div className="flex items-center gap-2 bg-slate-900/50 rounded-lg p-3 hover:bg-slate-900/80 transition-colors">
              <ExternalLink className="w-4 h-4 text-slate-400 group-hover:text-white shrink-0" />
              <span className="text-sm text-blue-300 group-hover:text-blue-200 truncate font-mono">
                {href}
              </span>
            </div>
          </a>
        ) : (
          <div className="mb-3 bg-slate-900/50 rounded-lg p-3">
            <div className="flex items-center gap-2">
              <ExternalLink className="w-4 h-4 text-slate-500 shrink-0" />
              <span className="text-sm text-slate-400 truncate font-mono">
                {link.link || '(no URL)'}
              </span>
            </div>
          </div>
        )}
        {link.ai_description && (
          <div className="mb-2 bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-2 text-xs text-emerald-300/90">
            <span className="font-semibold">🤖 وصف AI:</span> {link.ai_description}
          </div>
        )}
        <div className="grid grid-cols-2 gap-2 text-xs">
          {link.group_name && (
            <div className="flex items-center gap-1.5 text-slate-400">
              <MapPin className="w-3 h-3 shrink-0" />
              <span className="truncate">{link.group_name}</span>
            </div>
          )}
          {link.sender_name && (
            <div className="flex items-center gap-1.5 text-slate-400">
              <Users className="w-3 h-3 shrink-0" />
              <span className="truncate">{link.sender_name}</span>
            </div>
          )}
          {link.sender_contact && (
            <div className="flex items-center gap-1.5 text-slate-400">
              <Phone className="w-3 h-3 shrink-0" />
              <span className="truncate">{link.sender_contact}</span>
            </div>
          )}
        </div>
        {link.message_text && (
          <div className="mt-3 bg-slate-900/40 rounded-lg p-3 text-xs text-slate-400 max-h-24 overflow-hidden">
            {link.message_text}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ===== Modal Component =====
function LinksModal(props: {
  type: ModalType
  onClose: () => void
  allLinks: LinkItem[]
  joiners: Joiner[]
  joinersSummary: JoinersSummary | null
  joinedGroups: JoinedGroup[]
  monitoredChats: MonitoredChat[]
  monitoredSummary: MonitoredSummary | null
  bannedGroups: BannedGroup[]
  pendingApprovals: PendingApproval[]
  pendingSummary: PendingApprovalsSummary | null
  topGroups: TopGroup[]
  topGroupsTotals: TopGroupsTotals | null
  trendDays: number
}) {
  const { type, onClose, allLinks, joiners, joinersSummary, joinedGroups, monitoredChats, monitoredSummary, bannedGroups, pendingApprovals, pendingSummary, topGroups, topGroupsTotals, trendDays } = props
  const [searchQuery, setSearchQuery] = useState<string>('')

  // [UX] Esc closes the modal + / focuses the search box
  useEffect(() => {
    if (!type) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === '/' && !(e.target instanceof HTMLInputElement)) {
        e.preventDefault()
        document.getElementById('links-search-input')?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [type, onClose])

  const filteredLinks = useMemo((): LinkItem[] => {
    if (!type) return []
    let filtered = [...allLinks]

    if (type === 'whatsapp') {
      filtered = filtered.filter((l) => l.link_type === 'whatsapp')
    } else if (type === 'telegram') {
      filtered = filtered.filter((l) => l.link_type === 'telegram')
    } else if (type === 'ai_approved') {
      filtered = filtered.filter((l) => l.ai_approved === true)
    } else if (type === 'ai_rejected') {
      filtered = filtered.filter((l) => l.ai_approved === false)
    } else if (type === 'ai_ads') {
      filtered = filtered.filter((l) => l.ai_is_ad === true)
    }
    // 'all_links' shows everything

    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter(
        (l) =>
          l.link?.toLowerCase().includes(q) ||
          l.message_text?.toLowerCase().includes(q) ||
          l.group_name?.toLowerCase().includes(q) ||
          l.sender_name?.toLowerCase().includes(q)
      )
    }

    return filtered
  }, [type, allLinks, searchQuery])

  // [CSV-VIEW] export the CURRENT filtered view (respects search + tab).
  // Defined AFTER filteredLinks — the dep array needs its value.
  const onExportCsv = useCallback(() => {
    const stamp = new Date().toISOString().slice(0, 10)
    exportCsv(filteredLinks, `links-${type || 'all'}-${stamp}.csv`)
  }, [filteredLinks, type])

  const modalTitle = useMemo((): string => {
    switch (type) {
      case 'whatsapp':
        return '🟢 روابط واتساب'
      case 'telegram':
        return '🔵 روابط تيليجرام'
      case 'all_links':
        return '🔗 جميع الروابط'
      case 'ai_approved':
        return '✅ روابط موافق عليها (AI)'
      case 'ai_rejected':
        return '❌ روابط مرفوضة (AI)'
      case 'ai_ads':
        return '⚠️ روابط مُصنّفة كإعلانات (AI)'
      case 'joiners':
        return '🚀 لوحة الفدائي التفصيلية'
      case 'joined_groups':
        return '📋 المجموعات المنضم لها'
      case 'monitored_chats':
        return '👁️ المجموعات المراقبة (تصنيف AI)'
      case 'banned_groups':
        return '🚫 المجموعات الممنوعة'
      case 'pending_approvals':
        return '✉️ بانتظار موافقة المشرف'
      case 'top_groups':
        return `📊 أعلى المصادر إنتاجاً (آخر ${trendDays} يوم)`
      default:
        return ''
    }
  }, [type, trendDays])

  if (!type) return null

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
        onClick={onClose}
      >
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="bg-slate-900 border border-slate-700 rounded-xl max-w-5xl w-full max-h-[95vh] overflow-hidden flex flex-col"
          onClick={(e: ReactMouseEvent) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-slate-700">
            <h2 className="text-xl font-bold text-white">{modalTitle}</h2>
            <div className="flex items-center gap-3">
              {type !== 'joiners' && type !== 'pending_approvals' && type !== 'top_groups' && (
                <>
                  <Badge className="bg-slate-700 text-white border-0">
                    {filteredLinks.length}
                  </Badge>
                  {/* [CSV-VIEW] export current filtered view */}
                  <button
                    onClick={onExportCsv}
                    disabled={filteredLinks.length === 0}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 text-xs hover:bg-emerald-500/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    title="تصدير النتائج الحالية CSV (مع احترام البحث والتصفية)"
                  >
                    <Download className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">CSV</span>
                  </button>
                </>
              )}
              {type === 'pending_approvals' && pendingSummary && (
                <Badge className="bg-sky-500/20 text-sky-300 border-sky-500/40">
                  {pendingSummary.total_pending_approval}
                </Badge>
              )}
              <button
                onClick={onClose}
                className="text-slate-400 hover:text-white transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Content */}
          {type === 'joiners' ? (
            <div className="p-4 overflow-y-auto flex-1">
              {joinersSummary && (
                <div className="grid grid-cols-3 gap-3 mb-4">
                  <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-4 text-center">
                    <p className="text-3xl font-bold text-emerald-400">
                      {joinersSummary.total_joined_groups}
                    </p>
                    <p className="text-xs text-slate-400 mt-1">مجموعة منضم إليها</p>
                  </div>
                  <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-4 text-center">
                    <p className="text-3xl font-bold text-amber-400">
                      {joinersSummary.total_already_member}
                    </p>
                    <p className="text-xs text-slate-400 mt-1">منضم مسبقاً</p>
                  </div>
                  <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-center">
                    <p className="text-3xl font-bold text-red-400">
                      {joinersSummary.total_banned}
                    </p>
                    <p className="text-xs text-slate-400 mt-1">مجموعات ممنوعة</p>
                  </div>
                </div>
              )}

              <h3 className="text-sm font-semibold text-slate-300 mb-3">
                حسابات الفدائيين:
              </h3>
              <div className="space-y-3">
                {joiners.map((j) => (
                  <div
                    key={j.phone}
                    className="bg-slate-800/50 rounded-lg p-4 border border-slate-700"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div>
                        <p className="text-white font-mono text-sm">{j.phone}</p>
                        <p className="text-xs text-slate-400">
                          {j.display_name || 'بدون اسم'}
                        </p>
                      </div>
                      <span
                        className={`text-xs px-3 py-1 rounded-full ${
                          j.connected
                            ? 'bg-emerald-500/20 text-emerald-400'
                            : 'bg-red-500/20 text-red-400'
                        }`}
                      >
                        {j.connected ? '✅ متصل' : '❌ غير متصل'}
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-xs">
                      <div className="bg-slate-900/50 rounded p-2 text-center">
                        <p className="text-slate-400">انضمامات اليوم</p>
                        <p className="text-blue-400 font-bold">
                          {j.daily_joins}/{j.daily_limit}
                        </p>
                      </div>
                      <div className="bg-slate-900/50 rounded p-2 text-center">
                        <p className="text-slate-400">الحالة</p>
                        <p
                          className={
                            j.joiner_enabled
                              ? 'text-emerald-400 font-bold'
                              : 'text-red-400 font-bold'
                          }
                        >
                          {j.joiner_enabled ? 'مفعّل' : 'معطّل'}
                        </p>
                      </div>
                      <div className="bg-slate-900/50 rounded p-2 text-center">
                        <p className="text-slate-400">آخر انضمام</p>
                        <p className="text-slate-300 font-bold">
                          {j.last_join_timestamp
                            ? new Date(j.last_join_timestamp).toLocaleString('ar-SA', {
                                hour: '2-digit',
                                minute: '2-digit',
                                day: 'numeric',
                                month: 'short',
                              })
                            : 'أبداً'}
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : type === 'joined_groups' ? (
            <div className="flex-1 overflow-y-auto p-4" style={{ maxHeight: 'calc(95vh - 100px)' }}>
              {joinedGroups.length === 0 ? (
                <div className="text-center py-20">
                  <Users className="w-12 h-12 mx-auto text-slate-600 mb-4" />
                  <p className="text-slate-500 mb-2">لا توجد مجموعات منضم لها بعد</p>
                  <p className="text-slate-600 text-xs">
                    البوت ينتظر مجموعات 1000+ عضو لينضم لها
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {joinedGroups.map((g, i) => (
                    <div
                      key={i}
                      className="bg-slate-800/50 rounded-lg p-3 border border-slate-700"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex-1 min-w-0">
                          <p className="text-white text-sm font-semibold truncate">
                            {g.group_title || 'غير معروف'}
                          </p>
                          <a
                            href={g.group_link}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-blue-400 hover:text-blue-300 truncate block font-mono"
                          >
                            {g.group_link}
                          </a>
                        </div>
                        <Badge
                          variant="outline"
                          className={
                            g.state === 'JOINED'
                              ? 'border-emerald-500/30 text-emerald-400 ml-2'
                              : 'border-blue-500/30 text-blue-400 ml-2'
                          }
                        >
                          {g.state === 'JOINED' ? '✅ ناجح' : '👤 منضم مسبقاً'}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between text-xs text-slate-400">
                        <span>
                          👥 {g.member_count > 0 ? `${g.member_count.toLocaleString()} عضو` : 'غير معروف'}
                        </span>
                        <span className="font-mono">{g.joined_by_phone || '?'}</span>
                        {g.join_date && (
                          <span>
                            {new Date(g.join_date).toLocaleString('ar-SA', {
                              day: 'numeric',
                              month: 'short',
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : type === 'monitored_chats' ? (
            <div className="flex-1 overflow-y-auto p-4" style={{ maxHeight: 'calc(95vh - 100px)' }}>
              {/* Summary Stats */}
              {monitoredSummary && (
                <div className="grid grid-cols-4 gap-3 mb-4">
                  <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 text-center">
                    <p className="text-2xl font-bold text-blue-400">{monitoredSummary.total}</p>
                    <p className="text-xs text-slate-400 mt-1">إجمالي</p>
                  </div>
                  <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
                    <p className="text-2xl font-bold text-emerald-400">{monitoredSummary.classified}</p>
                    <p className="text-xs text-slate-400 mt-1">مُصنّفة</p>
                  </div>
                  <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center">
                    <p className="text-2xl font-bold text-amber-400">{monitoredSummary.educational}</p>
                    <p className="text-xs text-slate-400 mt-1">تعليمية</p>
                  </div>
                  <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-3 text-center">
                    <p className="text-2xl font-bold text-purple-400">{monitoredSummary.high_relevance}</p>
                    <p className="text-xs text-slate-400 mt-1">صلة عالية</p>
                  </div>
                </div>
              )}

              {/* Chats List */}
              {monitoredChats.length === 0 ? (
                <div className="text-center py-20">
                  <Users className="w-12 h-12 mx-auto text-slate-600 mb-4" />
                  <p className="text-slate-500">لا توجد مجموعات مراقبة بعد</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {monitoredChats.map((c) => (
                    <div
                      key={c.chat_id}
                      className="bg-slate-800/50 rounded-lg p-3 border border-slate-700"
                    >
                      <div className="flex items-start justify-between mb-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-white text-sm font-semibold truncate">
                              {c.chat_title || 'غير معروف'}
                            </span>
                            {c.username && (
                              <span className="text-xs text-blue-400 font-mono">
                                @{c.username}
                              </span>
                            )}
                          </div>
                          {c.ai_description && (
                            <p className="text-xs text-emerald-300/80 mb-1">
                              🤖 {c.ai_description}
                            </p>
                          )}
                          <div className="text-xs text-slate-400">
                            👁️ بواسطة: {c.monitored_by || '?'} · {' '}
                            {c.member_count > 0 ? `${c.member_count.toLocaleString()} عضو` : 'غير معروف'}
                          </div>
                        </div>
                        <div className="flex flex-col items-end gap-1 ml-2">
                          {/* Type Badge */}
                          {c.ai_classification && c.ai_classification !== 'unknown' && c.ai_classification !== 'error' && (
                            <Badge variant="outline" className="text-[10px]">
                              {c.ai_classification === 'group' ? '👥 مجموعة' :
                               c.ai_classification === 'channel' ? '📢 قناة' : c.ai_classification}
                            </Badge>
                          )}
                          {/* Relevance Score */}
                          {c.ai_relevance > 0 && (
                            <Badge
                              variant="outline"
                              className={`text-[10px] ${
                                c.ai_relevance >= 80
                                  ? 'border-emerald-500/40 text-emerald-400'
                                  : c.ai_relevance >= 50
                                  ? 'border-amber-500/40 text-amber-400'
                                  : 'border-red-500/40 text-red-400'
                              }`}
                            >
                              {c.ai_relevance}% صلة
                            </Badge>
                          )}
                          {/* Country */}
                          {c.ai_country && c.ai_country !== 'أخرى' && (
                            <Badge variant="outline" className="text-[10px] border-purple-500/30 text-purple-400">
                              {c.ai_country}
                            </Badge>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : type === 'banned_groups' ? (
            <div className="flex-1 overflow-y-auto p-4" style={{ maxHeight: 'calc(95vh - 100px)' }}>
              {bannedGroups.length === 0 ? (
                <div className="text-center py-20">
                  <XCircle className="w-12 h-12 mx-auto text-slate-600 mb-4" />
                  <p className="text-slate-500">لا توجد مجموعات ممنوعة</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {bannedGroups.map((g, i) => (
                    <div
                      key={i}
                      className="bg-slate-800/50 rounded-lg p-3 border border-red-900/30"
                    >
                      <div className="flex items-start justify-between mb-2">
                        <div className="flex-1 min-w-0">
                          <p className="text-white text-sm font-semibold truncate mb-1">
                            {g.group_title || 'غير معروف'}
                          </p>
                          <a
                            href={g.group_link}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-blue-400 hover:text-blue-300 truncate block font-mono"
                          >
                            {g.group_link}
                          </a>
                        </div>
                        <Badge variant="outline" className="border-red-500/40 text-red-400 ml-2">
                          🚫 ممنوعة
                        </Badge>
                      </div>
                      {g.last_error && (
                        <div className="bg-red-500/5 border border-red-500/20 rounded p-2 text-xs text-red-300/80 mt-2">
                          <span className="font-semibold">السبب:</span> {g.last_error}
                        </div>
                      )}
                      <div className="flex items-center justify-between text-xs text-slate-400 mt-2">
                        <span>
                          👥 {g.member_count > 0 ? `${g.member_count.toLocaleString()} عضو` : 'غير معروف'}
                        </span>
                        {g.join_date && (
                          <span>
                            {new Date(g.join_date).toLocaleString('ar-SA', {
                              day: 'numeric',
                              month: 'short',
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : type === 'pending_approvals' ? (
            <div className="flex-1 overflow-y-auto p-4" style={{ maxHeight: 'calc(95vh - 100px)' }}>
              {pendingSummary && pendingSummary.self_healing && (
                <div className="bg-sky-500/5 border border-sky-500/20 rounded-lg p-3 mb-4 text-xs text-sky-300/90 flex items-start gap-2">
                  <Clock3 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  <p>
                    حلقة الفحص الذاتي تتحقق من كل طلب كل{' '}
                    {Math.round(pendingSummary.recheck_interval_seconds / 60)} دقيقة — عند قبول
                    المشرف تنتقل المجموعة تلقائيًا إلى «منضم». لا حاجة لأي إجراء منك.
                  </p>
                </div>
              )}
              {pendingApprovals.length === 0 ? (
                <div className="text-center py-20">
                  <CheckCircle2 className="w-12 h-12 mx-auto text-slate-600 mb-4" />
                  <p className="text-slate-500">لا توجد طلبات معلّقة</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {pendingApprovals.map((p) => (
                    <div
                      key={p.id}
                      className="bg-slate-800/50 rounded-lg p-3 border border-sky-900/30"
                    >
                      <div className="flex items-start justify-between mb-2">
                        <div className="flex-1 min-w-0">
                          <a
                            href={safeUrl(p.group_link) || '#'}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-blue-400 hover:text-blue-300 truncate block font-mono"
                            dir="ltr"
                          >
                            {p.group_link || '؟'}
                          </a>
                        </div>
                        <Badge variant="outline" className="border-sky-500/40 text-sky-400 ml-2">
                          ✉️ معلّق
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between text-xs text-slate-400 mt-2">
                        <span className="font-mono" dir="ltr">
                          حساب الانضمام: {p.joined_by_phone || '؟'}
                        </span>
                        {p.since && (
                          <span>
                            منذ{' '}
                            {new Date(p.since).toLocaleString('ar-SA', {
                              day: 'numeric',
                              month: 'short',
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </span>
                        )}
                      </div>
                      {p.last_error && (
                        <div className="bg-slate-500/5 border border-slate-500/20 rounded p-2 text-xs text-slate-400 mt-2">
                          <span className="font-semibold">ملاحظة:</span> {p.last_error}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : type === 'top_groups' ? (
            <div className="flex-1 overflow-y-auto p-4" style={{ maxHeight: 'calc(95vh - 100px)' }}>
              {topGroupsTotals && (
                <div className="bg-fuchsia-500/5 border border-fuchsia-500/20 rounded-lg p-3 mb-4 text-xs text-fuchsia-300/90 flex items-start gap-2">
                  <BarChart3 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  <p>
                    تم التقاط{' '}
                    <span className="font-bold text-white">
                      {topGroupsTotals.total.toLocaleString()}
                    </span>{' '}
                    رابط من{' '}
                    <span className="font-bold text-white">
                      {topGroupsTotals.distinct_groups.toLocaleString()}
                    </span>{' '}
                    مصدر خلال آخر {trendDays} يوم. النسبة محسوبة من إجمالي النافذة.
                  </p>
                </div>
              )}
              {topGroupsTotals && topGroupsTotals.distinct_groups > topGroups.length && (
                <p className="text-[10px] text-slate-500 mb-3">
                  * يتم عرض أعلى {topGroups.length} مصدر فقط من إجمالي{' '}
                  {topGroupsTotals.distinct_groups.toLocaleString()} مصدر.
                </p>
              )}
              {topGroups.length === 0 ? (
                <div className="text-center py-20">
                  <BarChart3 className="w-12 h-12 mx-auto text-slate-600 mb-4" />
                  <p className="text-slate-500">لا توجد بيانات مصادر</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {topGroups.map((g, i) => (
                    <div
                      key={g.group}
                      className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50"
                    >
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-xs w-7 text-center flex-shrink-0 font-mono text-slate-500">
                            {i + 1}
                          </span>
                          <span
                            className={`text-sm truncate ${g.group === 'غير محدد' ? 'text-slate-500 italic' : 'text-slate-200'}`}
                            title={g.group}
                          >
                            {g.group}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <Badge className="bg-slate-700/50 text-slate-300 border-slate-600/40 text-[10px] tabular-nums">
                            {g.share.toFixed(1)}%
                          </Badge>
                          <span className="text-sm font-bold text-white tabular-nums">
                            {g.total.toLocaleString()}
                          </span>
                        </div>
                      </div>
                      <div className="flex h-1.5 rounded-full bg-slate-700/40 overflow-hidden mb-2">
                        <div
                          className="h-full bg-gradient-to-r from-emerald-400 to-emerald-600"
                          style={{ width: `${g.total ? (g.whatsapp / g.total) * 100 : 0}%` }}
                        />
                        <div
                          className="h-full bg-gradient-to-r from-blue-400 to-blue-600"
                          style={{ width: `${g.total ? (g.telegram / g.total) * 100 : 0}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between text-[10px] text-slate-400 flex-wrap gap-1">
                        <span className="flex items-center gap-2">
                          <span className="text-emerald-500">
                            واتساب {g.whatsapp.toLocaleString()}
                          </span>
                          <span className="text-blue-400">
                            تيليجرام {g.telegram.toLocaleString()}
                          </span>
                          {g.other > 0 && <span>أخرى {g.other.toLocaleString()}</span>}
                        </span>
                        <span className="text-slate-500" dir="ltr">
                          {g.first_seen} → {g.last_seen}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col flex-1 overflow-hidden">
              {/* Search */}
              <div className="p-4 border-b border-slate-700">
                <div className="relative">
                  <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <Input
                    id="links-search-input"
                    placeholder="ابحث في الروابط... (اضغط / للتركيز)"
                    value={searchQuery}
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setSearchQuery(e.target.value)
                    }
                    className="bg-slate-800/50 border-slate-700 text-white placeholder:text-slate-500 pr-10"
                  />
                </div>
              </div>

              {/* Links list — div عادي مع scroll بدل ScrollArea */}
              <div className="flex-1 overflow-y-auto p-4" style={{ maxHeight: 'calc(95vh - 200px)' }}>
                {filteredLinks.length === 0 ? (
                  <div className="text-center py-20">
                    <Link2 className="w-12 h-12 mx-auto text-slate-600 mb-4" />
                    <p className="text-slate-500">لا توجد روابط</p>
                  </div>
                ) : (
                  <div>
                    {filteredLinks.map((link) => (
                      <LinkCard
                        key={link.id}
                        link={link}
                        isMonitored={monitoredChats.some(
                          (c) =>
                            c.chat_title === link.group_name ||
                            (link.message_link &&
                              link.message_link.includes(
                                `/c/${String(c.chat_id).replace('-100', '')}/`
                              ))
                        )}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}
