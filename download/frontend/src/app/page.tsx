'use client'

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { motion, AnimatePresence, MotionConfig } from 'framer-motion'
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
  VolumeX, ShieldCheck, UserCircle2, BellRing, Target, ArrowUp,
  ArrowLeftRight, ChevronLeft, ChevronRight, ZoomIn, Users2,
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

// [DRILL-HISTORY] one stop in the WHO↔WHERE cross-drill chain. The
// navigation stack keeps every drill the operator left via a cross-drill
// swap so the breadcrumb bar can walk (or jump) back through it.
interface DrillStop {
  kind: 'group' | 'sender'
  name: string
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

// [SENDERS-VIEW] /api/top_senders response — WHO posts the links. The
// leaderboard is PII-free by construction (the backend never selects
// sender_contact); top_group + groups_count give the WHERE context.
interface TopSender {
  sender: string
  total: number
  whatsapp: number
  telegram: number
  other: number
  share: number
  groups_count: number
  top_group: string
  first_seen: string
  last_seen: string
}

interface TopSendersTotals {
  total: number
  distinct_senders: number
}

// [GROUP-DRILL] /api/group_detail response — per-group drill-down opened
// by clicking a top-group / quiet-source row. PII-free by construction
// (the backend selects sender_name only — never contacts/phones).
interface GroupDetailSender {
  sender: string
  total: number
  whatsapp: number
  telegram: number
  other: number
  share: number
  first_seen: string
  last_seen: string
}

interface GroupDetailData {
  group: string
  days: number
  totals: {
    total: number
    whatsapp: number
    telegram: number
    other: number
    distinct_senders: number
  }
  first_seen: string | null
  last_seen: string | null
  daily: DailyStat[]
  senders: GroupDetailSender[]
}

// [SENDER-DRILL] per-sender detail payload from /api/sender_detail.
interface SenderDetailGroup {
  group: string
  total: number
  whatsapp: number
  telegram: number
  other: number
  share: number
  first_seen: string
  last_seen: string
}

interface SenderDetailData {
  sender: string
  days: number
  totals: {
    total: number
    whatsapp: number
    telegram: number
    other: number
    distinct_groups: number
  }
  first_seen: string | null
  last_seen: string | null
  daily: DailyStat[]
  groups: SenderDetailGroup[]
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
  | 'top_senders'
  | null

// [ALERT-VIEW] one consolidated attention item surfaced by the strip.
interface AttentionItem {
  id: string
  tone: 'rose' | 'amber' | 'sky'
  text: string
}

// [NAV] quick-jump sections for the sticky chip bar.
const NAV_SECTIONS: { id: string; label: string }[] = [
  { id: 'sec-overview', label: 'نظرة عامة' },
  { id: 'sec-trend', label: 'الاتجاه' },
  { id: 'sec-targets', label: 'الأهداف' },
  { id: 'sec-sources', label: 'المصادر' },
  { id: 'sec-senders', label: 'المرسلون' },
  { id: 'sec-health', label: 'الصحة' },
  { id: 'sec-fleet', label: 'الأسطول' },
  { id: 'sec-ai', label: 'الذكاء' },
  { id: 'sec-monitored', label: 'المراقبة' },
  { id: 'sec-joiners', label: 'الفدائيون' },
]

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

// ===== [TARGET-VIEW] link-target classification =====
// Live-data validated (3,000-row sample): the capture stream is dominated by
// t.me public usernames (~68%), WhatsApp invites (~19%) and private Telegram
// invites (~12%) — and links are ~100% unique (dup ratio 1.0x). So the
// valuable breakdown is not "which domains" (only 2 hosts exist) but WHAT
// KIND of target each link points at, and how many are directly joinable
// (the joiner fleet can act on invite links immediately).
type TargetKind = 'wa_invite' | 'tg_public' | 'tg_private' | 'tg_other' | 'other'

interface TargetKindMeta {
  key: TargetKind
  label: string
  hint: string
  dot: string // tailwind bg for the dot
  bar: string // tailwind gradient for the share bar
  text: string // tailwind text color for counts
}

const TARGET_KINDS: TargetKindMeta[] = [
  {
    key: 'wa_invite',
    label: 'دعوات واتساب',
    hint: 'chat.whatsapp.com — قابلة للانضمام فوراً عبر الأسطول',
    dot: 'bg-emerald-500',
    bar: 'from-emerald-400 to-emerald-600',
    text: 'text-emerald-300',
  },
  {
    key: 'tg_private',
    label: 'دعوات تيليجرام خاصة',
    hint: 't.me/+… و t.me/joinchat — قابلة للانضمام فوراً',
    dot: 'bg-violet-500',
    bar: 'from-violet-400 to-violet-600',
    text: 'text-violet-300',
  },
  {
    key: 'tg_public',
    label: 'معرّفات تيليجرام عامة',
    hint: 't.me/<username> — قنوات ومجموعات عامة معروفة',
    dot: 'bg-blue-500',
    bar: 'from-blue-400 to-blue-600',
    text: 'text-blue-300',
  },
  {
    key: 'tg_other',
    label: 'روابط تيليجرام أخرى',
    hint: 't.me/c/… — رسائل/محتوى داخلي غير قابل للانضمام',
    dot: 'bg-sky-500',
    bar: 'from-sky-400 to-sky-600',
    text: 'text-sky-300',
  },
  {
    key: 'other',
    label: 'روابط أخرى',
    hint: 'مواقع ونطاقات خارج المنصتين',
    dot: 'bg-slate-500',
    bar: 'from-slate-400 to-slate-600',
    text: 'text-slate-300',
  },
]

// Host + path extraction tolerant of bare-host links ("t.me/foo").
function targetHostAndPath(url: string): { host: string; path: string } | null {
  const trimmed = url.trim()
  if (!trimmed) return null
  try {
    const withProto = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`
    const u = new URL(withProto)
    return {
      host: u.hostname.toLowerCase().replace(/^www\./, ''),
      path: u.pathname || '/',
    }
  } catch {
    return null
  }
}

function classifyTargetKind(url: string): TargetKind | null {
  const hp = targetHostAndPath(url)
  if (!hp) return null
  const { host, path } = hp
  if (host === 'chat.whatsapp.com' || host === 'invite.whatsapp.com' || host === 'whatsapp.com') {
    return 'wa_invite'
  }
  if (host === 't.me' || host === 'telegram.me' || host === 'telegram.dog') {
    if (path.startsWith('/+') || path.startsWith('/joinchat')) return 'tg_private'
    if (/^\/c\/\d+/i.test(path)) return 'tg_other'
    const seg = path.replace(/^\//, '').split('/')[0] ?? ''
    if (seg && !/^\d+$/.test(seg)) return 'tg_public'
    return 'tg_other'
  }
  return 'other'
}

// Dedup key: strip query/hash fragments and trailing slashes, lowercase host.
function normalizeTargetUrl(url: string): string | null {
  const hp = targetHostAndPath(url)
  if (!hp) return null
  return `${hp.host}${hp.path.replace(/\/+$/, '')}`
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
  // [SENDERS-VIEW] top link posters (WHO) — same window as the trend chart.
  const [topSenders, setTopSenders] = useState<TopSender[]>([])
  const [topSendersTotals, setTopSendersTotals] = useState<TopSendersTotals | null>(null)
  // [ALERT-VIEW] dismissed attention-strip ids (session-lifetime).
  const [dismissedAlerts, setDismissedAlerts] = useState<Set<string>>(new Set())
  // [DELTA-VIEW] independent 30-day daily series for stat-card deltas and
  // sparklines. NOT tied to trendDays: the delta always compares the last
  // 7 days against the previous 7, so it needs a fixed wide window (the
  // server caches per-days key for 60s — the extra poll is cheap).
  const [deltaDaily, setDeltaDaily] = useState<DailyStat[]>([])
  // [GROUP-DRILL] the group whose detail modal is open (null = closed).
  // The modal self-fetches /api/group_detail with the shared trend window.
  const [groupDetail, setGroupDetail] = useState<string | null>(null)
  // [SENDER-DRILL] the sender whose detail modal is open (null = closed).
  // The modal self-fetches /api/sender_detail with the shared trend window.
  const [senderDetail, setSenderDetail] = useState<string | null>(null)
  // [DRILL-HISTORY] navigation stack for the WHO↔WHERE cross-drill chain.
  // Every cross-drill swap pushes the drill being left; the breadcrumb
  // bar lets the operator walk back through the chain (or jump several
  // steps) instead of Esc-to-start. Reset whenever a fresh drill opens.
  const [drillHistory, setDrillHistory] = useState<DrillStop[]>([])
  // [TREND-COMPARE] overlay the previous period as ghost bars behind the
  // current window (positionally sliced from the 30-day delta series).
  const [compareMode, setCompareMode] = useState<boolean>(false)
  // [NAV-SPY] currently visible section (drives the active chip highlight)
  // and the back-to-top button visibility.
  const [activeSection, setActiveSection] = useState<string>('sec-overview')
  const [showBackToTop, setShowBackToTop] = useState<boolean>(false)
  const navStripRef = useRef<HTMLDivElement | null>(null)

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

  // [SENDERS-VIEW] top link posters over the shared window — completes the
  // WHAT (trend) / WHEN (hourly) / WHERE (groups) picture with WHO. The
  // endpoint is PII-free (no sender_contact ever fetched), so the card needs
  // no masking logic. limit=50 (server max) so the full modal list arrives;
  // the card slices the top 6.
  const fetchTopSenders = useCallback(async (days: number) => {
    try {
      const response = await fetch(
        `${API_URL}/api/top_senders?days=${days}&limit=50`,
        { headers: buildHeaders() }
      )
      if (response.ok) {
        const data = await response.json()
        if (Array.isArray(data.senders)) setTopSenders(data.senders)
        if (data.totals) setTopSendersTotals(data.totals)
      }
    } catch (err) {
      console.error('fetchTopSenders error:', err)
    }
  }, [])

  // [DELTA-VIEW] wide (30-day) daily series — feeds the stat-card delta
  // badges (last-7 vs previous-7 days) and sparklines. Runs on the 60s
  // trend cycle, not the 15s core cycle, to keep request volume flat.
  const fetchDeltaSeries = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/links_daily?days=30`, {
        headers: buildHeaders(),
      })
      if (response.ok) {
        const data = await response.json()
        if (Array.isArray(data.daily)) setDeltaDaily(data.daily)
      }
    } catch (err) {
      console.error('fetchDeltaSeries error:', err)
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
  // hourly strip, the top-groups card AND the top-senders card all switch
  // windows together. The [DELTA-VIEW] wide series rides the same cycle.
  useEffect(() => {
    const load = () => {
      fetchTrend(trendDays)
      fetchTopGroups(trendDays)
      fetchTopSenders(trendDays)
      fetchSourceHealth()
      fetchDeltaSeries()
    }
    load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [fetchTrend, fetchTopGroups, fetchTopSenders, fetchSourceHealth, fetchDeltaSeries, trendDays])

  // ===== [DRILL-HISTORY] chain navigation =====
  // Fresh entry (card row / view-all modal / deep link) starts a NEW chain:
  // any stale history from a previous exploration is discarded.
  const openDrillFresh = useCallback((kind: 'group' | 'sender', name: string) => {
    setDrillHistory([])
    if (kind === 'group') {
      setSenderDetail(null)
      setGroupDetail(name)
    } else {
      setGroupDetail(null)
      setSenderDetail(name)
    }
  }, [])

  // Cross-drill swap (from INSIDE a drill modal): push the drill being
  // left onto the stack, then open the target. Capped at 12 stops so a
  // long exploration session can't overflow the bar.
  const crossDrill = useCallback(
    (kind: 'group' | 'sender', name: string) => {
      const current: DrillStop[] = []
      if (groupDetail) current.push({ kind: 'group', name: groupDetail })
      if (senderDetail) current.push({ kind: 'sender', name: senderDetail })
      setDrillHistory([...drillHistory, ...current].slice(-12))
      if (kind === 'group') {
        setSenderDetail(null)
        setGroupDetail(name)
      } else {
        setGroupDetail(null)
        setSenderDetail(name)
      }
    },
    [groupDetail, senderDetail, drillHistory]
  )

  // Jump back to an earlier stop (its index in drillHistory). The stack is
  // truncated after it — the chosen stop becomes the current drill. Pass
  // the LAST index for a simple one-step back.
  const drillBackTo = useCallback(
    (index: number) => {
      const stop = drillHistory[index]
      if (!stop) return
      if (stop.kind === 'group') {
        setSenderDetail(null)
        setGroupDetail(stop.name)
      } else {
        setGroupDetail(null)
        setSenderDetail(stop.name)
      }
      setDrillHistory(drillHistory.slice(0, index))
    },
    [drillHistory]
  )

  // Close everything (X / Esc / overlay click) and reset the chain.
  const closeDrill = useCallback(() => {
    setGroupDetail(null)
    setSenderDetail(null)
    setDrillHistory([])
  }, [])

  // ===== [DRILL-DEEPLINK + BACK-FWD] shareable drill URLs + browser
  // back/forward integration =====
  // The open drill is mirrored into the URL hash (#g=… / #s=…). A drill
  // session occupies exactly ONE browser-history entry:
  //   * opening the FIRST drill of a session → pushState, so browser
  //     Back closes the whole modal (the #1 instinct on mobile);
  //   * cross-drills / breadcrumb jumps within the session →
  //     replaceState (Back exits the modal; the breadcrumb bar walks
  //     the chain — two separate mental models, two controls);
  //   * closing via X/Esc/overlay → history.back() when we own the
  //     pushed entry; popstate then lands on the hash-less dashboard
  //     entry. Deep-linked loads (no pushed entry of ours) clear the
  //     hash via replaceState instead.
  //   * popstate (browser Back/Forward) → sync state FROM the hash: a
  //     drill hash re-opens that drill (Forward after closing, or Back
  //     inside a deep-link session); an empty hash closes any open
  //     drill.
  // The mount-parse effect below still re-opens a shared #hash as a
  // fresh chain on load (bookmark/share flows unchanged).
  const drillEntryPushedRef = useRef<boolean>(false) // we pushed this session's entry
  const exitDrillRef = useRef<boolean>(false) // closing via our own history.back()
  const firstSyncRef = useRef<boolean>(true) // skip mount run (parse effect owns it)

  useEffect(() => {
    const m = window.location.hash.match(/^#(g|s)=(.+)$/)
    if (!m) return
    try {
      const name = decodeURIComponent(m[2])
      if (name) openDrillFresh(m[1] === 'g' ? 'group' : 'sender', name)
    } catch {
      // malformed hash — ignore, dashboard loads normally
    }
    // run once on mount: parse the entry hash
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // State → URL: keep the hash in sync with the open drill (ONE history
  // entry per drill session — see the block comment above).
  useEffect(() => {
    if (firstSyncRef.current) {
      firstSyncRef.current = false
      return
    }
    const want = groupDetail
      ? `#g=${encodeURIComponent(groupDetail)}`
      : senderDetail
        ? `#s=${encodeURIComponent(senderDetail)}`
        : ''
    const cur = window.location.hash
    if (cur === want) return
    if (want) {
      if (drillEntryPushedRef.current) {
        // mid-session navigation (cross-drill / breadcrumb jump)
        window.history.replaceState(null, '', want)
      } else {
        // first drill of a session → one pushed entry; Back closes it
        window.history.pushState(null, '', want)
        drillEntryPushedRef.current = true
      }
    } else if (drillEntryPushedRef.current) {
      // closing a session we pushed → pop our entry; popstate lands
      // hash-less and the exitDrillRef guard covers the deep-link edge
      // where the entry below ours still carries a stale drill hash.
      drillEntryPushedRef.current = false
      exitDrillRef.current = true
      window.history.back()
    } else {
      // deep-linked load (nothing of ours to pop) → clear the hash
      window.history.replaceState(null, '', window.location.pathname)
    }
  }, [groupDetail, senderDetail])

  // URL → state: browser Back/Forward while a drill hash is (or was)
  // open. This is what makes the modal feel like a real page-level view.
  useEffect(() => {
    const onPop = () => {
      const m = window.location.hash.match(/^#(g|s)=(.+)$/)
      if (m) {
        if (exitDrillRef.current) {
          // our own close-via-back() landed on a stale drill entry
          // (deep-link case) — replace it with the dashboard entry
          exitDrillRef.current = false
          window.history.replaceState(null, '', window.location.pathname)
          return
        }
        try {
          const name = decodeURIComponent(m[2])
          if (!name) return
          const kind = m[1] === 'g' ? 'group' : 'sender'
          if ((kind === 'group' && groupDetail === name)
            || (kind === 'sender' && senderDetail === name)) return
          openDrillFresh(kind, name)
          drillEntryPushedRef.current = true
        } catch {
          // malformed hash — ignore
        }
      } else if (exitDrillRef.current) {
        exitDrillRef.current = false
      } else if (groupDetail || senderDetail) {
        // Back from the drill entry → dashboard: close the modal
        setGroupDetail(null)
        setSenderDetail(null)
        setDrillHistory([])
        drillEntryPushedRef.current = false
      }
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [groupDetail, senderDetail, openDrillFresh])

  const refreshAll = useCallback(() => {
    fetchLinks()
    fetchStats()
    fetchJoiners()
    fetchMonitoredChats()
    fetchReadiness()
    fetchPendingApprovals()
    fetchTrend(trendDays)
    fetchTopGroups(trendDays)
    fetchTopSenders(trendDays)
    fetchSourceHealth()
    fetchDeltaSeries()
    setLastUpdated(new Date())
  }, [fetchLinks, fetchStats, fetchJoiners, fetchMonitoredChats, fetchReadiness, fetchPendingApprovals, fetchTrend, fetchTopGroups, fetchTopSenders, fetchSourceHealth, fetchDeltaSeries, trendDays])

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

  // [ALERT-VIEW] consolidated attention items — everything that needs the
  // operator's eye WITHOUT scrolling: stopped top sources (the Aug-19
  // cluster lesson: the info existed in the quiet-sources card but required
  // scrolling to notice), AI backlog ratio, fleet-down. Rendered as one
  // dismissible strip under the header so nothing critical hides below
  // the fold. Ids stay stable across refreshes so a dismissal sticks.
  const attentionItems = useMemo((): AttentionItem[] => {
    const items: AttentionItem[] = []
    const stopped = quietSources.filter((q) => q.daysQuiet >= STOPPED_AFTER_DAYS)
    if (stopped.length > 0) {
      const top = stopped[0]
      const extra = stopped.length - 1
      items.push({
        id: 'stopped-sources',
        tone: 'rose',
        text: `${stopped.length} مصدر متوقف (≥${STOPPED_AFTER_DAYS} أيام بدون روابط) — الأكبر: «${top.group}» (${top.total.toLocaleString()} رابط، صامت منذ ${top.daysQuiet} يوم)${extra > 0 ? ` و+${extra} آخرين` : ''}`,
      })
    }
    const ai = stats?.ai_stats
    if (ai && ai.ai_pending + ai.ai_approved + ai.ai_rejected + ai.ai_ads > 200) {
      const processed = ai.ai_approved + ai.ai_rejected + ai.ai_ads
      const total = processed + ai.ai_pending
      const ratio = total > 0 ? (ai.ai_pending / total) * 100 : 0
      if (ratio > 90) {
        items.push({
          id: 'ai-backlog',
          tone: 'amber',
          text: `تراكم فحص الذكاء الاصطناعي: ${ai.ai_pending.toLocaleString()} رابط بانتظار الفحص (${ratio.toFixed(1)}% من الإجمالي) — فعّل AI_DRAIN_ENABLED لتشغيل المعالجة الخلفية`,
        })
      }
    }
    if (readiness?.fleet_health?.all_joiners_unavailable) {
      items.push({
        id: 'fleet-down',
        tone: 'rose',
        text: 'جميع حسابات الانضمام غير متاحة — لا يمكن الانضمام للمجموعات الجديدة حتى عودة الأسطول',
      })
    }
    return items
  }, [quietSources, stats, readiness])

  const visibleAlerts = attentionItems.filter((a) => !dismissedAlerts.has(a.id))

  // [TARGET-VIEW] pattern breakdown of the captured links — computed entirely
  // client-side from the existing /api/links payload (up to 5,000 rows, no
  // new endpoint). Answers "what KIND of targets flow through": WhatsApp
  // invites (directly joinable), public Telegram usernames, private TG
  // invites (directly joinable), internal t.me/c/ links, other hosts.
  // Also counts UNIQUE targets (live data: ~100% unique — dup ratio ~1.0x,
  // so the card reports the fact instead of a pointless re-share list).
  const targetStats = useMemo(() => {
    const counts: Record<TargetKind, number> = {
      wa_invite: 0,
      tg_public: 0,
      tg_private: 0,
      tg_other: 0,
      other: 0,
    }
    const seen = new Set<string>()
    let parsed = 0
    for (const l of allLinks) {
      const url = (l?.link ?? '').toString().trim()
      if (!url) continue
      const kind = classifyTargetKind(url)
      if (!kind) continue
      parsed++
      counts[kind]++
      const norm = normalizeTargetUrl(url)
      if (norm) seen.add(norm)
    }
    const joinable = counts.wa_invite + counts.tg_private
    return {
      counts,
      parsed,
      unique: seen.size,
      joinable,
      joinablePct: parsed > 0 ? (joinable / parsed) * 100 : 0,
    }
  }, [allLinks])

  // [DELTA-VIEW] stat-card deltas — last 7 days vs the previous 7 days from
  // the wide 30-day series. Returns null when there is no baseline (series
  // shorter than 14 days or previous week summed to 0) so the badge simply
  // doesn't render instead of showing a meaningless ∞%.
  const statDeltas = useMemo(() => {
    const series = deltaDaily
    if (!series || series.length < 14) return null
    const last7 = series.slice(-7)
    const prev7 = series.slice(-14, -7)
    const sum = (arr: DailyStat[], key: 'total' | 'whatsapp' | 'telegram') =>
      arr.reduce((acc, d) => acc + (Number(d?.[key]) || 0), 0)
    const pct = (cur: number, prev: number): number | null =>
      prev > 0 ? ((cur - prev) / prev) * 100 : null
    return {
      total: pct(sum(last7, 'total'), sum(prev7, 'total')),
      whatsapp: pct(sum(last7, 'whatsapp'), sum(prev7, 'whatsapp')),
      telegram: pct(sum(last7, 'telegram'), sum(prev7, 'telegram')),
      sparkTotal: last7.concat(prev7).map((d) => Number(d.total) || 0),
      sparkWhatsapp: last7.concat(prev7).map((d) => Number(d.whatsapp) || 0),
      sparkTelegram: last7.concat(prev7).map((d) => Number(d.telegram) || 0),
    }
  }, [deltaDaily])

  // [TREND-COMPARE] the previous window of the same length as the current
  // trend window, sliced positionally from the END of the 30-day delta
  // series (date-robust: aligned by position, same convention as the delta
  // badges). Null when there isn't enough history (e.g. a 30-day window
  // needs 60 days) — the toggle hides and compareMode force-resets.
  const prevWindow = useMemo(() => {
    if (!deltaDaily || deltaDaily.length < trendDays * 2) return null
    return deltaDaily.slice(-trendDays * 2, -trendDays)
  }, [deltaDaily, trendDays])

  useEffect(() => {
    if (!prevWindow) setCompareMode(false)
  }, [prevWindow])

  // [NAV-SPY] scroll-spy — highlights the quick-nav chip of the section
  // currently in view. A top band (rootMargin) decides "active": the section
  // whose top edge crosses the upper 20–40% of the viewport wins. Sections
  // missing from the DOM (hidden cards) are skipped silently.
  useEffect(() => {
    const els = NAV_SECTIONS.map((s) => document.getElementById(s.id)).filter(
      (el): el is HTMLElement => el !== null
    )
    if (els.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActiveSection(entry.target.id)
        }
      },
      { rootMargin: '-15% 0px -65% 0px', threshold: 0 }
    )
    els.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [trendDaily, topGroups, topSenders, allLinks, quietSources.length])

  // [NAV-SPY] keep the active chip visible inside the horizontal strip on
  // narrow screens (auto-center it). RTL-safe: uses offsetLeft relative to
  // the strip + scrollTo (no scrollIntoView → no page jump side-effects).
  useEffect(() => {
    const strip = navStripRef.current
    if (!strip) return
    const idx = NAV_SECTIONS.findIndex((s) => s.id === activeSection)
    if (idx < 0) return
    const chip = strip.children[idx] as HTMLElement | undefined
    if (!chip) return
    try {
      const target = chip.offsetLeft - strip.clientWidth / 2 + chip.clientWidth / 2
      strip.scrollTo({ left: target, behavior: 'smooth' })
    } catch {
      /* non-fatal — highlight alone is still correct */
    }
  }, [activeSection])

  // [NAV-SPY] back-to-top visibility — appears once the operator scrolls
  // past ~1.5 viewport heights.
  useEffect(() => {
    const onScroll = () => setShowBackToTop(window.scrollY > window.innerHeight * 1.5)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    // [A11Y] reducedMotion="user": framer-motion disables transform/layout
    // animations for users with prefers-reduced-motion (opacity fades stay).
    <MotionConfig reducedMotion="user">
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

        {/* [NAV] Sticky quick-jump chips — the dashboard grew to ~10 cards;
            without navigation the operator scrolls blind. Chips anchor-jump
            to sections and stay pinned while scrolling (backdrop blur).
            Horizontally scrollable on mobile via overflow-x-auto.
            [NAV-SPY] the chip of the section currently in view gets the
            emerald active style (scroll-spy via IntersectionObserver). */}
        <nav
          aria-label="التنقل السريع"
          className="sticky top-0 z-40 -mx-4 px-4 py-2 mb-5 bg-slate-950/80 backdrop-blur-md border-b border-slate-800/60"
        >
          <div ref={navStripRef} className="flex items-center gap-1.5 overflow-x-auto no-scrollbar">
            {NAV_SECTIONS.map((s) => {
              const active = activeSection === s.id
              return (
                <a
                  key={s.id}
                  href={`#${s.id}`}
                  aria-current={active ? 'location' : undefined}
                  onClick={(e) => {
                    e.preventDefault()
                    document.getElementById(s.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }}
                  className={`px-2.5 py-1 rounded-full text-[11px] font-medium whitespace-nowrap transition-all flex-shrink-0 border ${
                    active
                      ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/50 shadow-[0_0_12px_-2px_rgba(16,185,129,0.4)]'
                      : 'text-slate-400 hover:text-white hover:bg-slate-800/80 border-slate-700/50'
                  }`}
                >
                  {s.label}
                </a>
              )
            })}
          </div>
        </nav>

        {/* [ALERT-VIEW] Attention strip — dismissible consolidated alerts.
            The Aug-19 lesson: the quiet-sources card KNEW 5+ sources died on
            the same day, but the operator had to scroll to see it. This strip
            puts "needs your eye" facts above the fold. */}
        {visibleAlerts.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-6 space-y-2"
            role="alert"
            aria-live="polite"
          >
            {visibleAlerts.map((a) => (
              <div
                key={a.id}
                className={`flex items-start gap-3 p-3 rounded-xl border backdrop-blur-sm ${
                  a.tone === 'rose'
                    ? 'border-rose-500/40 bg-rose-950/30'
                    : a.tone === 'amber'
                      ? 'border-amber-500/40 bg-amber-950/30'
                      : 'border-sky-500/40 bg-sky-950/30'
                }`}
              >
                <BellRing
                  className={`w-4 h-4 mt-0.5 flex-shrink-0 ${
                    a.tone === 'rose' ? 'text-rose-400' : a.tone === 'amber' ? 'text-amber-400' : 'text-sky-400'
                  }`}
                />
                <p
                  className={`flex-1 min-w-0 text-xs leading-relaxed ${
                    a.tone === 'rose' ? 'text-rose-200/90' : a.tone === 'amber' ? 'text-amber-200/90' : 'text-sky-200/90'
                  }`}
                >
                  {a.text}
                </p>
                <button
                  onClick={() =>
                    setDismissedAlerts((prev) => new Set(prev).add(a.id))
                  }
                  className={`transition-colors flex-shrink-0 ${
                    a.tone === 'rose'
                      ? 'text-rose-300/70 hover:text-white'
                      : a.tone === 'amber'
                        ? 'text-amber-300/70 hover:text-white'
                        : 'text-sky-300/70 hover:text-white'
                  }`}
                  aria-label="تجاهل هذا التنبيه"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            ))}
          </motion.div>
        )}

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
          id="sec-overview"
          className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4 mb-6 scroll-mt-20"
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
                delta={statDeltas?.total ?? null}
                spark={statDeltas?.sparkTotal}
                sparkColor="#34d399"
              />
              <StatCard
                icon={<MessageCircle className="w-5 h-5" />}
                label="🟢 واتساب"
                value={stats?.whatsapp_links ?? 0}
                gradient="from-green-500/20 to-green-500/5"
                iconColor="text-green-400"
                onClick={() => setModal('whatsapp')}
                delta={statDeltas?.whatsapp ?? null}
                spark={statDeltas?.sparkWhatsapp}
                sparkColor="#4ade80"
              />
              <StatCard
                icon={<Send className="w-5 h-5" />}
                label="🔵 تيليجرام"
                value={stats?.telegram_links ?? 0}
                gradient="from-blue-500/20 to-blue-500/5"
                iconColor="text-blue-400"
                onClick={() => setModal('telegram')}
                delta={statDeltas?.telegram ?? null}
                spark={statDeltas?.sparkTelegram}
                sparkColor="#60a5fa"
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
          <Card id="sec-trend" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
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
                  {/* [TREND-COMPARE] overlay the previous window as ghost
                      bars — answers "are we up or down vs last week?"
                      directly on the chart. Hidden when the 30-day delta
                      series can't cover a previous window of this length. */}
                  {prevWindow && (
                    <button
                      onClick={() => setCompareMode((v) => !v)}
                      role="switch"
                      aria-checked={compareMode}
                      aria-label="مقارنة بالفترة السابقة"
                      title="إظهار أعمدة الفترة السابقة (باهتة) خلف الأعمدة الحالية"
                      className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium border transition-colors ${
                        compareMode
                          ? 'bg-amber-500/20 text-amber-300 border-amber-500/50'
                          : 'bg-slate-900/70 text-slate-400 border-slate-700/50 hover:text-slate-200'
                      }`}
                    >
                      <ArrowLeftRight className="w-3 h-3" />
                      <span className="hidden sm:inline">مقارنة</span>
                    </button>
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
              <TrendChart
                daily={trendDaily}
                totals={trendTotals}
                prev={compareMode && prevWindow ? prevWindow : undefined}
              />
              <div className="flex items-center justify-center gap-4 mt-1 text-[10px] text-slate-400 flex-wrap">
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
                {compareMode && prevWindow && (
                  <span className="flex items-center gap-1.5 text-slate-500">
                    <span className="w-2.5 h-2.5 rounded-sm bg-slate-500/40 border border-slate-500/60" />
                    الفترة السابقة
                  </span>
                )}
              </div>
              {/* [HEATMAP-VIEW] hour-of-day activity strip — WHEN do links
                  get posted? Peak hour highlighted in amber. */}
              {hourly.length > 0 && <HourlyStrip hourly={hourly} />}
            </CardContent>
          </Card>
        )}

        {/* [TARGET-VIEW] what KIND of targets flow through the capture
            stream? Client-side classification of the latest /api/links
            batch (up to 5,000): WhatsApp invites, private Telegram invites
            (both DIRECTLY JOINABLE — the fleet can act on them), public
            TG usernames, internal t.me/c/ links and other hosts. Live-data
            validated: ~68% public usernames, ~19% WA invites, ~12% private
            invites; links are ~100% unique (the card reports the unique
            count so the operator knows the real target yield). */}
        {targetStats.parsed > 0 && (
          <Card id="sec-targets" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center justify-between text-lg flex-wrap gap-2">
                <span className="flex items-center gap-2">
                  <Target className="w-5 h-5 text-teal-400" />
                  تركيبة الأهداف الملتقطة
                  <span className="text-xs text-slate-500 font-normal">
                    (آخر {targetStats.parsed.toLocaleString()} رابط)
                  </span>
                </span>
                <Badge className="bg-slate-700/50 text-slate-300 border-slate-600/40 text-[10px]">
                  {targetStats.unique.toLocaleString()} هدف فريد
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {TARGET_KINDS.map((meta, i) => {
                  const count = targetStats.counts[meta.key]
                  if (count === 0) return null
                  const share = targetStats.parsed > 0 ? (count / targetStats.parsed) * 100 : 0
                  const maxCount = Math.max(
                    ...TARGET_KINDS.map((m) => targetStats.counts[m.key])
                  )
                  const width = maxCount > 0 ? Math.max(3, (count / maxCount) * 100) : 0
                  return (
                    <motion.div
                      key={meta.key}
                      initial={{ opacity: 0, x: 12 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.25, delay: i * 0.05 }}
                      className="group"
                      title={meta.hint}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="flex items-center gap-2 text-xs text-slate-300">
                          <span className={`w-2 h-2 rounded-full ${meta.dot}`} />
                          {meta.label}
                          <span className="text-slate-500 text-[10px] hidden sm:inline">
                            {meta.hint}
                          </span>
                        </span>
                        <span className={`text-xs font-bold ${meta.text}`}>
                          {count.toLocaleString()}
                          <span className="text-slate-500 font-normal mr-1.5">
                            {share.toFixed(1)}%
                          </span>
                        </span>
                      </div>
                      <div className="h-2 rounded-full bg-slate-900/70 overflow-hidden">
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${width}%` }}
                          transition={{ duration: 0.5, delay: 0.1 + i * 0.05 }}
                          className={`h-full rounded-full bg-gradient-to-l ${meta.bar}`}
                        />
                      </div>
                    </motion.div>
                  )
                })}
              </div>
              {/* Joinable insight — the actionable yield: invite links the
                  joiner fleet can act on immediately. */}
              <div className="mt-4 p-3 rounded-xl border border-teal-500/30 bg-teal-950/20">
                <p className="text-xs text-teal-200/90 leading-relaxed">
                  <span className="font-bold">
                    ⚡ قابلة للانضمام مباشرة: {targetStats.joinable.toLocaleString()} رابط
                  </span>{' '}
                  ({targetStats.joinablePct.toFixed(1)}% من الالتقاطات) — دعوات واتساب ودعوات
                  تيليجرام الخاصة جاهزة لأسطول الانضمام فوراً، بينما تتطلب المعرّفات العامة
                  تفتيشاً يدوياً.
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* [SOURCE-VIEW] Top link-producing groups — link-source attribution
            over the same window as the trend chart. Split bars show the
            WA/TG mix; hover a row for the full breakdown + last activity. */}
        {topGroups.length > 0 && (
          <Card id="sec-sources" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
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
                  <TopGroupRow
                    key={g.group}
                    rank={i + 1}
                    group={g}
                    max={topGroups[0]?.total || 1}
                    onClick={() => openDrillFresh('group', g.group)}
                  />
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
                <span className="flex items-center gap-1 text-slate-600">
                  <ZoomIn className="w-3 h-3" />
                  اضغط على أي مصدر لعرض مرسليه ومنحناه اليومي
                </span>
              </div>
            </CardContent>
          </Card>
        )}

        {/* [SENDERS-VIEW] Top link posters — WHO posts the links (the trend
            answers WHAT, the hourly strip WHEN, top-groups WHERE). Same
            window as the trend chart; split bars show the WA/TG mix; hover a
            row for the breakdown + the group they post in most. */}
        {topSenders.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35 }}
          >
            <Card id="sec-senders" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center justify-between text-lg">
                  <span className="flex items-center gap-2">
                    <UserCircle2 className="w-5 h-5 text-teal-400" />
                    أكثر المرسلين نشراً للروابط
                    <span className="text-xs text-slate-500 font-normal">
                      (آخر {trendDays} يوم · {topSendersTotals?.total.toLocaleString() || '—'} رابط)
                    </span>
                  </span>
                  {topSendersTotals && topSendersTotals.distinct_senders > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setModal('top_senders')}
                      className="text-slate-400 hover:text-white text-xs h-7"
                    >
                      عرض الكل ({topSendersTotals.distinct_senders.toLocaleString()})
                      <ArrowRight className="w-3.5 h-3.5 mr-1 rotate-180" />
                    </Button>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {topSenders.slice(0, 6).map((s, i) => (
                    <TopSenderRow
                      key={s.sender}
                      rank={i + 1}
                      sender={s}
                      max={topSenders[0]?.total || 1}
                      onClick={() => openDrillFresh('sender', s.sender)}
                    />
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
                  <span className="text-slate-600">· بدون بيانات هواتف (خصوصية)</span>
                </div>
                <p className="text-[10px] text-slate-600 text-center mt-2 flex items-center justify-center gap-1">
                  <ZoomIn className="w-3 h-3" aria-hidden />
                  اضغط على أي مرسل لعرض منشوراته اليومية ومصادره
                </p>
              </CardContent>
            </Card>
          </motion.div>
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
            <Card id="sec-health" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
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
                          className="group bg-slate-900/50 rounded-lg p-3 border border-slate-700/50 hover:border-slate-600/60 transition-colors cursor-pointer"
                          onClick={() => openDrillFresh('group', g.group)}
                          role="button"
                          tabIndex={0}
                          aria-label={`عرض تفاصيل ${g.group}`}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault()
                              openDrillFresh('group', g.group)
                            }
                          }}
                          title={`نشاط ${g.first_seen} ← ${g.last_seen} · واتساب ${g.whatsapp.toLocaleString()} / تيليجرام ${g.telegram.toLocaleString()} — اضغط لعرض التفاصيل`}
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
                          <div className="text-[10px] text-slate-500 mt-1.5 flex items-center justify-between gap-2">
                            <div className="opacity-0 group-hover:opacity-100 transition-opacity truncate">
                              <span className="text-emerald-500">واتساب {g.whatsapp.toLocaleString()}</span>
                              {' · '}
                              <span className="text-blue-400">تيليجرام {g.telegram.toLocaleString()}</span>
                              {g.other > 0 && <span> · أخرى {g.other.toLocaleString()}</span>}
                              <span> · نشاط {g.first_seen} ← {g.last_seen}</span>
                            </div>
                            <span
                              className="flex items-center gap-0.5 text-slate-500 group-hover:text-emerald-400 flex-shrink-0 transition-colors"
                              aria-hidden
                            >
                              <ZoomIn className="w-3 h-3" />
                              تفاصيل
                            </span>
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
          <Card id="sec-fleet" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
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
          <Card id="sec-ai" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
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
        <Card id="sec-monitored" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
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
        <Card id="sec-joiners" className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6 scroll-mt-20">
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
        topSenders={topSenders}
        topSendersTotals={topSendersTotals}
        trendDays={trendDays}
        onOpenGroupDetail={(g) => {
          setModal(null)
          openDrillFresh('group', g)
        }}
        onOpenSenderDetail={(s) => {
          setModal(null)
          openDrillFresh('sender', s)
        }}
      />

      {/* [GROUP-DRILL] per-group detail modal — opened by clicking a
          top-group / quiet-source row. [CROSS-DRILL] its sender rows swap
          to the sender drill-down (WHO↔WHERE both directions). */}
      <AnimatePresence>
        {groupDetail && (
          <GroupDetailModal
            key={groupDetail}
            group={groupDetail}
            days={trendDays}
            history={drillHistory}
            onJumpBack={drillBackTo}
            onClose={closeDrill}
            onOpenSender={(s) => crossDrill('sender', s)}
          />
        )}
      </AnimatePresence>

      {/* [SENDER-DRILL] per-sender detail modal — opened by clicking a
          top-sender row (card or view-all modal) or a sender row inside
          the group drill-down. Its group rows swap back to the group
          drill-down — the cross-analysis loop. */}
      <AnimatePresence>
        {senderDetail && (
          <SenderDetailModal
            key={senderDetail}
            sender={senderDetail}
            days={trendDays}
            history={drillHistory}
            onJumpBack={drillBackTo}
            onClose={closeDrill}
            onOpenGroup={(g) => crossDrill('group', g)}
          />
        )}
      </AnimatePresence>

      {/* [NAV-SPY] back-to-top — appears after ~1.5 viewport heights of
          scrolling (the dashboard is long); smooth-scrolls home. Fixed to
          the bottom-start corner, above content, below modals. */}
      <AnimatePresence>
        {showBackToTop && (
          <motion.button
            key="back-to-top"
            initial={{ opacity: 0, y: 16, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.9 }}
            transition={{ duration: 0.2 }}
            onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
            aria-label="العودة إلى الأعلى"
            className="fixed bottom-6 left-6 z-40 w-11 h-11 rounded-full bg-emerald-600/90 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-900/50 border border-emerald-400/30 flex items-center justify-center transition-colors"
          >
            <ArrowUp className="w-5 h-5" />
          </motion.button>
        )}
      </AnimatePresence>
    </div>
    </MotionConfig>
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
    // [A11Y] reduced-motion users get the final value instantly — no
    // rAF count-up animation.
    if (typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setVal(target)
      prev.current = target
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

// ===== useModalA11y — [A11Y/STYLING] dialog semantics + focus trap =====
// Every overlay modal (LinksModal / GroupDetailModal / SenderDetailModal)
// gets proper dialog semantics (role="dialog" + aria-modal + aria-label),
// initial focus on the dialog itself, a Tab focus trap (keyboard users
// can no longer tab into the page BEHIND the overlay — focus cycled
// between the first/last focusable child), focus restoration to the
// pre-modal element on close, and a body scroll-lock while open.
//
// A MODULE-LEVEL STACK coordinates overlapping modal lifecycles: during a
// cross-drill, AnimatePresence keeps the outgoing modal mounted for its
// exit animation while the incoming one is already active. The FIRST
// entry of a modal session owns the lock: it captures the pre-modal
// scroll/focus state, and ONLY the transition back to an empty stack
// restores it (intermediate entries capture the LOCKED state — restoring
// that would leave the page scrolled-locked forever, which is exactly
// the bug the stack exists to prevent).
const __modalA11yStack: object[] = []
let __modalA11yBase = {
  prevOverflow: '',
  prevFocus: null as HTMLElement | null,
}

function useModalA11y(active: boolean, label: string) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!active) return
    const entry = {}
    const isFirst = __modalA11yStack.length === 0
    if (isFirst) {
      __modalA11yBase = {
        prevOverflow: document.body.style.overflow,
        prevFocus: document.activeElement as HTMLElement | null,
      }
      document.body.style.overflow = 'hidden'
    }
    __modalA11yStack.push(entry)
    ref.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const root = ref.current
      if (!root) return
      const focusables = Array.from(
        root.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]),'
          + ' select:not([disabled]), textarea:not([disabled]),'
          + ' [tabindex]:not([tabindex="-1"])'
        )
      ).filter((el) => el.offsetParent !== null || el === document.activeElement)
      if (focusables.length === 0) {
        e.preventDefault()
        return
      }
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const activeEl = document.activeElement
      if (activeEl === root) {
        // focus sits on the dialog container itself: Tab enters at the
        // first child, Shift+Tab cycles to the LAST child (the browser's
        // default would leave the dialog backwards into the page).
        e.preventDefault()
        if (e.shiftKey) last.focus()
        else first.focus()
        return
      }
      if (e.shiftKey && activeEl === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && activeEl === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      const idx = __modalA11yStack.indexOf(entry)
      if (idx >= 0) __modalA11yStack.splice(idx, 1)
      if (__modalA11yStack.length === 0) {
        document.body.style.overflow = __modalA11yBase.prevOverflow
        // restore focus only if the pre-modal element still exists
        const pf = __modalA11yBase.prevFocus
        if (pf && pf.isConnected) {
          pf.focus?.()
        }
      }
    }
  }, [active])
  return useMemo(() => ({
    ref,
    dialogProps: {
      role: 'dialog' as const,
      'aria-modal': true as const,
      'aria-label': label,
      tabIndex: -1,
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [label])
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
function TrendChart(props: {
  daily: DailyStat[]
  totals: TrendTotals | null
  prev?: DailyStat[]
}) {
  const { daily, totals, prev } = props
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
  // [TREND-COMPARE] the y-scale must cover the ghost bars too — otherwise
  // a bigger previous period would overflow the top of the chart.
  const max = Math.max(
    ...daily.map((d) => d.total),
    ...(prev ? prev.map((d) => d.total) : []),
    1
  )
  const barW = chartW / daily.length
  const avg = totals?.avg_per_day ?? 0
  const avgY = padT + chartH - (avg / max) * chartH
  const hoverStat = hovered !== null ? daily[hovered] : null
  const hoverPrev = prev && hovered !== null ? prev[hovered] : null

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
          // [TREND-COMPARE] ghost bar of the previous period — drawn behind
          // the current colored bars (same slot, muted slate, slightly
          // wider) so "this week vs last week" reads at a glance.
          const prevTotal = prev && prev[i] ? Number(prev[i].total) || 0 : 0
          const prevH = prev ? (prevTotal / max) * chartH : 0
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
              {/* [TREND-COMPARE] previous-period ghost bar */}
              {prev && prevTotal > 0 && (
                <rect
                  x={padL + i * barW + barW * 0.06}
                  y={yBase - prevH}
                  width={barW * 0.88}
                  height={Math.max(prevH, 2)}
                  rx="2"
                  fill="#64748b"
                  opacity={isHover ? 0.45 : 0.28}
                  className="transition-opacity"
                  data-prev={prevTotal}
                />
              )}
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
          {hoverPrev && (
            <p className="text-slate-500">
              الفترة السابقة: {hoverPrev.total.toLocaleString()}
              {hoverPrev.total > 0 && hoverStat.total > 0 && (
                <span
                  className={
                    hoverStat.total >= hoverPrev.total
                      ? 'text-emerald-400 font-bold'
                      : 'text-rose-400 font-bold'
                  }
                >
                  {' '}
                  ({hoverStat.total >= hoverPrev.total ? '▲' : '▼'}
                  {Math.abs(
                    Math.round(
                      ((hoverStat.total - hoverPrev.total) / hoverPrev.total) * 100
                    )
                  )}
                  %)
                </span>
              )}
            </p>
          )}
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

// ===== TopSenderRow — [SENDERS-VIEW] one ranked sender row =====
// Split bar (green WA / blue TG) scaled to the #1 sender; medals for the
// top 3; cross-poster chip; hover reveals the breakdown + top group.
// [SENDER-DRILL] clickable when onClick is provided (keyboard-accessible).
function TopSenderRow(props: { rank: number; sender: TopSender; max: number; onClick?: () => void }) {
  const { rank, sender, max, onClick } = props
  const medals = ['🥇', '🥈', '🥉']
  const rankLabel = medals[rank - 1] || <span className="text-slate-500">#{rank}</span>
  const widthPct = Math.max(2, Math.round((sender.total / max) * 100))
  const waPct = sender.total
    ? Math.round((sender.whatsapp / sender.total) * widthPct)
    : 0
  const tgPct = widthPct - waPct
  const isUnnamed = sender.sender === 'غير محدد'

  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: Math.min(rank * 0.06, 0.4), duration: 0.35 }}
      className={onClick
        ? 'group cursor-pointer rounded-md p-1 -mx-1 hover:bg-slate-800/40 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50'
        : 'group'}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label={onClick ? `عرض تفاصيل ${sender.sender}` : undefined}
      onKeyDown={(e) => {
        if (onClick && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          onClick()
        }
      }}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs w-6 text-center flex-shrink-0" aria-hidden>
            {rankLabel}
          </span>
          <span
            className={`text-sm truncate ${isUnnamed ? 'text-slate-500 italic' : 'text-slate-200'}`}
            title={sender.sender}
          >
            {sender.sender}
          </span>
          {/* groups-count chip — cross-posters stand out */}
          {sender.groups_count > 1 && (
            <span
              className="text-[9px] px-1.5 py-0.5 rounded-full border border-teal-500/30 bg-teal-500/10 text-teal-300 whitespace-nowrap flex-shrink-0"
              title={`نشر في ${sender.groups_count} مجموعة مختلفة`}
            >
              {sender.groups_count} مجموعة
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-[10px] text-slate-500 tabular-nums">
            {sender.share.toFixed(1)}%
          </span>
          <span className="text-sm font-bold text-white tabular-nums">
            {sender.total.toLocaleString()}
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
      {/* hover detail line — WA/TG split + the group they post in most */}
      <div className="text-[10px] text-slate-500 mt-1 h-4 flex items-center justify-between gap-2">
        <div className="opacity-0 group-hover:opacity-100 transition-opacity truncate">
          <span className="text-emerald-500">واتساب {sender.whatsapp.toLocaleString()}</span>
          {' · '}
          <span className="text-blue-400">تيليجرام {sender.telegram.toLocaleString()}</span>
          {sender.other > 0 && <span> · أخرى {sender.other.toLocaleString()}</span>}
          <span> · الأكثر في: <span className="text-slate-400">{sender.top_group}</span></span>
          <span> · نشاط {sender.first_seen} ← {sender.last_seen}</span>
        </div>
        {onClick && (
          <span
            className="flex items-center gap-0.5 text-slate-500 group-hover:text-teal-400 flex-shrink-0 transition-colors"
            aria-hidden
          >
            <ZoomIn className="w-3 h-3" />
            تفاصيل
          </span>
        )}
      </div>
    </motion.div>
  )
}

// ===== TopGroupRow — [SOURCE-VIEW] one ranked source row =====
// Split bar (green WA / blue TG) scaled to the #1 group; medals for the
// top 3; hover reveals the full breakdown + first/last activity dates.
// [GROUP-DRILL] clickable when onClick is provided (keyboard-accessible).
function TopGroupRow(props: {
  rank: number
  group: TopGroup
  max: number
  onClick?: () => void
}) {
  const { rank, group, max, onClick } = props
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
      className={onClick
        ? 'group cursor-pointer rounded-md p-1 -mx-1 hover:bg-slate-800/40 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50'
        : 'group'}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label={onClick ? `عرض تفاصيل ${group.group}` : undefined}
      onKeyDown={(e) => {
        if (onClick && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          onClick()
        }
      }}
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
      <div className="text-[10px] text-slate-500 mt-1 h-4 flex items-center justify-between gap-2">
        <div className="opacity-0 group-hover:opacity-100 transition-opacity truncate">
          <span className="text-emerald-500">واتساب {group.whatsapp.toLocaleString()}</span>
          {' · '}
          <span className="text-blue-400">تيليجرام {group.telegram.toLocaleString()}</span>
          {group.other > 0 && <span> · أخرى {group.other.toLocaleString()}</span>}
          <span> · نشاط {group.first_seen} ← {group.last_seen}</span>
        </div>
        {onClick && (
          <span
            className="flex items-center gap-0.5 text-slate-500 group-hover:text-emerald-400 flex-shrink-0 transition-colors"
            aria-hidden
          >
            <ZoomIn className="w-3 h-3" />
            تفاصيل
          </span>
        )}
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
  // [DELTA-VIEW] period-over-period change (last 7d vs previous 7d, %).
  // null/undefined → no badge (no baseline yet, e.g. watchers card).
  delta?: number | null
  // [DELTA-VIEW] mini sparkline series (14 points: prev-7 + last-7).
  spark?: number[]
  sparkColor?: string
}) {
  const { icon, label, value, gradient, iconColor, onClick, delta, spark, sparkColor } = props
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
            <div className="min-w-0">
              <p className="text-slate-400 text-xs mb-1">{label}</p>
              <p className="text-2xl font-bold text-white">{animated.toLocaleString()}</p>
              {/* [DELTA-VIEW] week-over-week badge — ▲ emerald / ▼ rose /
                  flat slate. title carries the exact comparison. */}
              {typeof delta === 'number' && Number.isFinite(delta) && (
                <span
                  title={`مقارنةً بـ7 أيام السابقة: ${delta >= 0 ? '+' : ''}${delta.toFixed(1)}%`}
                  className={`inline-flex items-center gap-0.5 mt-1.5 px-1.5 py-0.5 rounded-md text-[10px] font-bold border ${
                    Math.abs(delta) < 1
                      ? 'bg-slate-700/40 text-slate-400 border-slate-600/40'
                      : delta > 0
                        ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                        : 'bg-rose-500/15 text-rose-400 border-rose-500/30'
                  }`}
                >
                  {Math.abs(delta) < 1
                    ? '–'
                    : delta > 0
                      ? '▲'
                      : '▼'}{' '}
                  {Math.abs(delta) < 1 ? 'ثابت' : `${Math.abs(delta).toFixed(0)}%`}
                </span>
              )}
            </div>
            <div className="flex flex-col items-end gap-1.5">
              <div className={`${iconColor} opacity-80 group-hover:opacity-100 group-hover:scale-110 transition-all`}>{icon}</div>
              {/* [DELTA-VIEW] sparkline — last 14 days of this metric; the
                  right half (recent week) is emphasized, the left half is
                  the baseline week at 55% opacity. */}
              {spark && spark.length >= 2 && (
                <Sparkline values={spark} color={sparkColor ?? '#34d399'} className="opacity-70 group-hover:opacity-100 transition-opacity" />
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </button>
  )
}

// ===== Sparkline — [DELTA-VIEW] tiny inline SVG trend (14 points) =====
function Sparkline(props: {
  values: number[]
  color?: string
  width?: number
  height?: number
  className?: string
}) {
  const { values, color = '#34d399', width = 76, height = 26, className } = props
  const n = values.length
  if (n < 2) return null
  const max = Math.max(...values)
  const min = Math.min(...values)
  const range = max - min || 1
  // x grows left→right (works fine in RTL context — time flows naturally)
  const pts = values.map((v, i) => {
    const x = (i / (n - 1)) * (width - 2) + 1
    const y = height - 3 - ((v - min) / range) * (height - 6)
    return [x, y] as const
  })
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const baseline = pts.slice(0, Math.floor(n / 2)) // previous week
  const baselineLine = baseline.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = `1,${height - 2} ${line} ${width - 1},${height - 2}`
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <polygon points={area} fill={color} opacity={0.12} />
      <polyline
        points={baselineLine}
        fill="none"
        stroke={color}
        strokeWidth={1.4}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.45}
      />
      <polyline
        points={line}
        fill="none"
        stroke={color}
        strokeWidth={1.8}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={pts[n - 1][0]} cy={pts[n - 1][1]} r={1.8} fill={color} />
    </svg>
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

// ===== DrillBreadcrumb — [DRILL-HISTORY] chain navigation bar =====
// Renders the WHO↔WHERE cross-drill chain as clickable chips: every
// previous stop jumps back to it (truncating the stack after it); the
// current drill is highlighted and NOT clickable (it's the modal title).
// Kind-colored to match the drill identities: fuchsia = group (WHERE),
// teal = sender (WHO). Horizontally scrollable for long chains / mobile.
function DrillBreadcrumb(props: {
  history: DrillStop[]
  current: DrillStop
  onBack: () => void
  onJumpBack: (index: number) => void
}) {
  const { history, current, onBack, onJumpBack } = props
  if (history.length === 0) return null
  const chipStyle = (kind: DrillStop['kind']) =>
    kind === 'group'
      ? 'border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-200 hover:bg-fuchsia-500/25'
      : 'border-teal-500/40 bg-teal-500/10 text-teal-200 hover:bg-teal-500/25'
  const kindGlyph = (kind: DrillStop['kind']) => (kind === 'group' ? '📁' : '👤')
  return (
    <div
      className="flex items-center gap-1.5 px-4 py-2 border-b border-slate-700/60 bg-slate-950/40 overflow-x-auto no-scrollbar"
      dir="rtl"
      role="navigation"
      aria-label="مسار التنقل بين التفاصيل"
    >
      {/* one-step back — the most common action gets a labeled button */}
      <button
        onClick={onBack}
        className="flex items-center gap-1 flex-shrink-0 text-[11px] text-slate-300 bg-slate-700/40 hover:bg-slate-600/60 border border-slate-600/50 rounded-md px-2 py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/60"
        aria-label="رجوع إلى التفاصيل السابقة"
      >
        <ChevronRight className="w-3.5 h-3.5" aria-hidden />
        رجوع
      </button>
      {history.map((stop, i) => (
        <span key={`${stop.kind}:${stop.name}:${i}`} className="flex items-center gap-1.5 flex-shrink-0">
          <button
            onClick={() => onJumpBack(i)}
            className={`max-w-[120px] truncate text-[11px] rounded-md border px-2 py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/60 ${chipStyle(stop.kind)}`}
            title={`${stop.name} — رجوع إلى هنا`}
            aria-label={`رجوع إلى ${stop.kind === 'group' ? 'المصدر' : 'المرسل'} ${stop.name}`}
          >
            {kindGlyph(stop.kind)} {stop.name}
          </button>
          {/* separator points in the RTL flow direction (right → left) */}
          <ChevronLeft className="w-3 h-3 text-slate-600 flex-shrink-0" aria-hidden />
        </span>
      ))}
      {/* current stop — highlighted, not clickable (it's the open modal) */}
      <span
        className={`max-w-[140px] truncate text-[11px] rounded-md border px-2 py-1 flex-shrink-0 font-semibold ${
          current.kind === 'group'
            ? 'border-fuchsia-400/60 bg-fuchsia-500/25 text-white'
            : 'border-teal-400/60 bg-teal-500/25 text-white'
        }`}
        title={current.name}
        aria-current="page"
      >
        {kindGlyph(current.kind)} {current.name}
      </span>
    </div>
  )
}

// ===== [GROUP-DRILL] GroupDetailModal — per-group drill-down =====
// Opened by clicking a top-group or quiet-source row. Self-fetches
// /api/group_detail?group=X&days=N (the shared trend window) and shows:
// the group's own daily series (reuses TrendChart — dry days are signal
// here), its top senders with WA/TG split bars, and the activity range.
// PII-free: the backend never selects contacts/phones for this view.
// [CROSS-DRILL] sender rows open the SENDER drill-down when
// onOpenSender is provided — the WHO↔WHERE cross-analysis becomes
// navigable in both directions.
function GroupDetailModal(props: {
  group: string
  days: number
  history: DrillStop[]
  onJumpBack: (index: number) => void
  onClose: () => void
  onOpenSender?: (sender: string) => void
}) {
  const { group, days, history, onJumpBack, onClose, onOpenSender } = props
  const [data, setData] = useState<GroupDetailData | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState<number>(0)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const resp = await fetch(
          `${API_URL}/api/group_detail?group=${encodeURIComponent(group)}&days=${days}`,
          { headers: buildHeaders() }
        )
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const json = await resp.json()
        if (!cancelled) setData(json as GroupDetailData)
      } catch {
        if (!cancelled) setError('تعذّر تحميل تفاصيل المصدر — تحقق من الاتصال وحاول مرة أخرى.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [group, days, reloadTick])

  // Esc closes (same pattern as LinksModal)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // [A11Y] dialog semantics + focus trap + scroll-lock for this modal
  const a11y = useModalA11y(true, `تفاصيل المصدر ${group}`)

  const maxSender = data?.senders[0]?.total || 1
  const waPctOfTotal = data && data.totals.total
    ? Math.round((data.totals.whatsapp / data.totals.total) * 100)
    : 0
  // [DRILL-DEEPLINK] shareable URL for THIS drill view (#g=…) — copied
  // to the clipboard by the header copy button. The modal only renders
  // client-side (after a user click), so window is always defined here;
  // the guard keeps the expression SSR-safe regardless.
  const shareUrl = typeof window !== 'undefined'
    ? `${window.location.origin}${window.location.pathname}#g=${encodeURIComponent(group)}`
    : ''

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
          ref={a11y.ref}
          {...a11y.dialogProps}
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="bg-slate-900 border border-slate-700 rounded-xl max-w-3xl w-full max-h-[95vh] overflow-hidden flex flex-col focus:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-500/60"
          onClick={(e: ReactMouseEvent) => e.stopPropagation()}
        >
          {/* Header — fuchsia identity with a subtle gradient tint */}
          <div className="flex items-center justify-between p-4 border-b border-slate-700 gap-3 bg-gradient-to-l from-fuchsia-500/10 to-transparent">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-9 h-9 rounded-lg bg-fuchsia-500/15 border border-fuchsia-500/30 flex items-center justify-center flex-shrink-0">
                <Users2 className="w-4.5 h-4.5 text-fuchsia-400" />
              </div>
              <div className="min-w-0">
                <h2 className="text-lg font-bold text-white truncate" title={group}>
                  {group}
                </h2>
                <p className="text-[11px] text-slate-500">
                  تفاصيل المصدر · آخر {days} يوم
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              {/* [DRILL-DEEPLINK] copy a shareable link to this drill view */}
              <CopyButton text={shareUrl} />
              <button
                onClick={onClose}
                className="text-slate-400 hover:text-white transition-colors flex-shrink-0"
                aria-label="إغلاق"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* [DRILL-HISTORY] breadcrumb bar — walk back through the
              cross-drill chain (or jump several steps) instead of
              starting over. Hidden while the chain has a single stop. */}
          <DrillBreadcrumb
            history={history}
            current={{ kind: 'group', name: group }}
            onBack={() => onJumpBack(history.length - 1)}
            onJumpBack={onJumpBack}
          />

          {/* Content */}
          <div className="p-4 overflow-y-auto flex-1 modal-scroll">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-14 gap-3">
                <RefreshCw className="w-8 h-8 text-emerald-400 animate-spin" />
                <p className="text-sm text-slate-400">جارٍ تحميل تفاصيل المصدر…</p>
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center py-12 gap-3">
                <AlertTriangle className="w-8 h-8 text-amber-400" />
                <p className="text-sm text-slate-300">{error}</p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setReloadTick((t) => t + 1)}
                  className="border-slate-600 text-slate-200 hover:bg-slate-800"
                >
                  <RefreshCw className="w-3.5 h-3.5 ml-1" />
                  إعادة المحاولة
                </Button>
              </div>
            ) : data ? (
              <div className="space-y-5">
                {/* totals — compact chips grid */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                  <div className="bg-slate-800/60 border border-slate-700/60 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-white tabular-nums">
                      {data.totals.total.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">إجمالي الروابط</p>
                  </div>
                  <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-emerald-400 tabular-nums">
                      {data.totals.whatsapp.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">واتساب ({waPctOfTotal}%)</p>
                  </div>
                  <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-blue-400 tabular-nums">
                      {data.totals.telegram.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">تيليجرام ({100 - waPctOfTotal - (data.totals.total ? Math.round((data.totals.other / data.totals.total) * 100) : 0)}%)</p>
                  </div>
                  <div className="bg-teal-500/10 border border-teal-500/30 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-teal-400 tabular-nums">
                      {data.totals.distinct_senders.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">مرسل مختلف</p>
                  </div>
                </div>

                {/* activity range */}
                {data.first_seen && (
                  <div className="flex items-center gap-2 text-[11px] text-slate-400 bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2">
                    <Clock3 className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                    <span>
                      نشاط المصدر داخل النافذة: <span className="text-slate-200 font-medium" dir="ltr">{data.first_seen}</span>
                      {' ← '}
                      <span className="text-slate-200 font-medium" dir="ltr">{data.last_seen}</span>
                    </span>
                  </div>
                )}

                {/* the group's own daily series — dry days are the story
                    for quiet sources (they show as zero bars) */}
                {data.daily.length > 0 && (
                  <div>
                    <h3 className="text-sm font-semibold text-slate-200 mb-2 flex items-center gap-1.5">
                      <TrendingUp className="w-4 h-4 text-emerald-400" />
                      الإنتاج اليومي للمصدر
                    </h3>
                    <TrendChart daily={data.daily} totals={null} />
                  </div>
                )}

                {/* top senders of THIS group */}
                <div>
                  <h3 className="text-sm font-semibold text-slate-200 mb-2.5 flex items-center gap-1.5">
                    <UserCircle2 className="w-4 h-4 text-teal-400" />
                    أكثر المرسلين في هذا المصدر
                  </h3>
                  {data.senders.length === 0 ? (
                    <p className="text-xs text-slate-500 text-center py-3">
                      لا روابط لهذا المصدر داخل النافذة المحددة.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {data.senders.slice(0, 10).map((s, i) => {
                        const widthPct = Math.max(3, Math.round((s.total / maxSender) * 100))
                        const sWaPct = s.total ? Math.round((s.whatsapp / s.total) * widthPct) : 0
                        const sTgPct = widthPct - sWaPct
                        return (
                          <div
                            key={`${s.sender}-${i}`}
                            className={onOpenSender
                              ? 'group/s rounded-md p-1 -mx-1 cursor-pointer hover:bg-slate-800/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50'
                              : 'group/s'}
                            onClick={onOpenSender ? () => onOpenSender(s.sender) : undefined}
                            role={onOpenSender ? 'button' : undefined}
                            tabIndex={onOpenSender ? 0 : undefined}
                            aria-label={onOpenSender ? `عرض تفاصيل ${s.sender}` : undefined}
                            onKeyDown={(e) => {
                              if (onOpenSender && (e.key === 'Enter' || e.key === ' ')) {
                                e.preventDefault()
                                onOpenSender(s.sender)
                              }
                            }}
                          >
                            <div className="flex items-center justify-between gap-2 mb-1">
                              <div className="flex items-center gap-2 min-w-0">
                                <span className="text-[10px] text-slate-500 w-5 text-center flex-shrink-0 tabular-nums">
                                  {i + 1}.
                                </span>
                                <span
                                  className={`text-xs truncate ${s.sender === 'غير محدد' ? 'text-slate-500 italic' : 'text-slate-200'}`}
                                  title={s.sender}
                                >
                                  {s.sender}
                                </span>
                              </div>
                              <div className="flex items-center gap-2 flex-shrink-0">
                                <span className="text-[10px] text-slate-500 tabular-nums">
                                  {s.share.toFixed(1)}%
                                </span>
                                <span className="text-xs font-bold text-white tabular-nums">
                                  {s.total.toLocaleString()}
                                </span>
                              </div>
                            </div>
                            <div className="flex h-1.5 rounded-full bg-slate-700/40 overflow-hidden">
                              <div
                                className="h-full bg-gradient-to-r from-emerald-400 to-emerald-600"
                                style={{ width: `${sWaPct}%` }}
                              />
                              <div
                                className="h-full bg-gradient-to-r from-blue-400 to-blue-600"
                                style={{ width: `${sTgPct}%` }}
                              />
                            </div>
                            <div className="text-[9px] text-slate-600 mt-0.5 h-3 opacity-0 group-hover/s:opacity-100 transition-opacity flex items-center justify-between gap-2">
                              <span className="truncate">
                                واتساب {s.whatsapp.toLocaleString()} · تيليجرام {s.telegram.toLocaleString()}
                                {s.other > 0 && <span> · أخرى {s.other.toLocaleString()}</span>}
                                <span> · نشاط {s.first_seen} ← {s.last_seen}</span>
                              </span>
                              {onOpenSender && (
                                <span
                                  className="flex items-center gap-0.5 text-slate-500 group-hover/s:text-teal-400 flex-shrink-0 transition-colors"
                                  aria-hidden
                                >
                                  <ZoomIn className="w-2.5 h-2.5" />
                                  تفاصيل
                                </span>
                              )}
                            </div>
                          </div>
                        )
                      })}
                      {data.senders.length > 10 && (
                        <p className="text-[10px] text-slate-500 text-center">
                          + {data.senders.length - 10} مرسلين آخرين
                        </p>
                      )}
                    </div>
                  )}
                </div>

                <p className="text-[10px] text-slate-600 text-center border-t border-slate-800 pt-3">
                  🔒 بدون بيانات هواتف أو جهات اتصال (خصوصية) · خلفية الرسم البياني: انقطاع المصدر يظهر كأيام صفرية
                </p>
              </div>
            ) : null}
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}

// ===== [SENDER-DRILL] SenderDetailModal — per-sender drill-down =====
// Mirror of GroupDetailModal for senders: self-fetches
// /api/sender_detail?sender=X&days=N (the shared trend window) and shows
// the sender's own daily series (TrendChart reuse — a gone-quiet sender
// shows trailing zeros), the groups they posted in with WA/TG split
// bars, and the activity range. PII-free by construction (the backend
// never selects contacts/phones for this view).
// [CROSS-DRILL] group rows open the GROUP drill-down when onOpenGroup is
// provided — WHO↔WHERE navigation in both directions.
function SenderDetailModal(props: {
  sender: string
  days: number
  history: DrillStop[]
  onJumpBack: (index: number) => void
  onClose: () => void
  onOpenGroup?: (group: string) => void
}) {
  const { sender, days, history, onJumpBack, onClose, onOpenGroup } = props
  const [data, setData] = useState<SenderDetailData | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState<number>(0)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const resp = await fetch(
          `${API_URL}/api/sender_detail?sender=${encodeURIComponent(sender)}&days=${days}`,
          { headers: buildHeaders() }
        )
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const json = await resp.json()
        if (!cancelled) setData(json as SenderDetailData)
      } catch {
        if (!cancelled) setError('تعذّر تحميل تفاصيل المرسل — تحقق من الاتصال وحاول مرة أخرى.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [sender, days, reloadTick])

  // Esc closes (same pattern as LinksModal / GroupDetailModal)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // [A11Y] dialog semantics + focus trap + scroll-lock for this modal
  const a11y = useModalA11y(true, `تفاصيل المرسل ${sender}`)

  const maxGroup = data?.groups[0]?.total || 1
  const waPctOfTotal = data && data.totals.total
    ? Math.round((data.totals.whatsapp / data.totals.total) * 100)
    : 0
  const tgPctOfTotal = data && data.totals.total
    ? Math.round((data.totals.telegram / data.totals.total) * 100)
    : 0
  // [DRILL-DEEPLINK] shareable URL for THIS drill view (#s=…) — see
  // GroupDetailModal.shareUrl for the SSR-safety rationale.
  const shareUrl = typeof window !== 'undefined'
    ? `${window.location.origin}${window.location.pathname}#s=${encodeURIComponent(sender)}`
    : ''

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
          ref={a11y.ref}
          {...a11y.dialogProps}
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="bg-slate-900 border border-slate-700 rounded-xl max-w-3xl w-full max-h-[95vh] overflow-hidden flex flex-col focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/60"
          onClick={(e: ReactMouseEvent) => e.stopPropagation()}
        >
          {/* Header — teal (sender identity) with a subtle gradient tint */}
          <div className="flex items-center justify-between p-4 border-b border-slate-700 gap-3 bg-gradient-to-l from-teal-500/10 to-transparent">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-9 h-9 rounded-lg bg-teal-500/15 border border-teal-500/30 flex items-center justify-center flex-shrink-0">
                <UserCircle2 className="w-4.5 h-4.5 text-teal-400" />
              </div>
              <div className="min-w-0">
                <h2 className="text-lg font-bold text-white truncate" title={sender}>
                  {sender}
                </h2>
                <p className="text-[11px] text-slate-500">
                  تفاصيل المرسل · آخر {days} يوم
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              {/* [DRILL-DEEPLINK] copy a shareable link to this drill view */}
              <CopyButton text={shareUrl} />
              <button
                onClick={onClose}
                className="text-slate-400 hover:text-white transition-colors flex-shrink-0"
                aria-label="إغلاق"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* [DRILL-HISTORY] breadcrumb bar — see GroupDetailModal */}
          <DrillBreadcrumb
            history={history}
            current={{ kind: 'sender', name: sender }}
            onBack={() => onJumpBack(history.length - 1)}
            onJumpBack={onJumpBack}
          />

          {/* Content */}
          <div className="p-4 overflow-y-auto flex-1 modal-scroll">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-14 gap-3">
                <RefreshCw className="w-8 h-8 text-teal-400 animate-spin" />
                <p className="text-sm text-slate-400">جارٍ تحميل تفاصيل المرسل…</p>
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center py-12 gap-3">
                <AlertTriangle className="w-8 h-8 text-amber-400" />
                <p className="text-sm text-slate-300">{error}</p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setReloadTick((t) => t + 1)}
                  className="border-slate-600 text-slate-200 hover:bg-slate-800"
                >
                  <RefreshCw className="w-3.5 h-3.5 ml-1" />
                  إعادة المحاولة
                </Button>
              </div>
            ) : data ? (
              <div className="space-y-5">
                {/* totals — compact chips grid */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                  <div className="bg-slate-800/60 border border-slate-700/60 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-white tabular-nums">
                      {data.totals.total.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">إجمالي الروابط</p>
                  </div>
                  <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-emerald-400 tabular-nums">
                      {data.totals.whatsapp.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">واتساب ({waPctOfTotal}%)</p>
                  </div>
                  <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-blue-400 tabular-nums">
                      {data.totals.telegram.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">تيليجرام ({tgPctOfTotal}%)</p>
                  </div>
                  <div className="bg-fuchsia-500/10 border border-fuchsia-500/30 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-fuchsia-400 tabular-nums">
                      {data.totals.distinct_groups.toLocaleString()}
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">مجموعة مختلفة</p>
                  </div>
                </div>

                {/* activity range */}
                {data.first_seen && (
                  <div className="flex items-center gap-2 text-[11px] text-slate-400 bg-slate-800/40 border border-slate-700/50 rounded-lg px-3 py-2">
                    <Clock3 className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                    <span>
                      نشاط المرسل داخل النافذة: <span className="text-slate-200 font-medium" dir="ltr">{data.first_seen}</span>
                      {' ← '}
                      <span className="text-slate-200 font-medium" dir="ltr">{data.last_seen}</span>
                    </span>
                  </div>
                )}

                {/* the sender's own daily series — a gone-quiet sender
                    shows as trailing zero bars (the timeline story) */}
                {data.daily.length > 0 && (
                  <div>
                    <h3 className="text-sm font-semibold text-slate-200 mb-2 flex items-center gap-1.5">
                      <TrendingUp className="w-4 h-4 text-teal-400" />
                      النشر اليومي للمرسل
                    </h3>
                    <TrendChart daily={data.daily} totals={null} />
                  </div>
                )}

                {/* the groups this sender posted in — [CROSS-DRILL] rows
                    open the group drill-down */}
                <div>
                  <h3 className="text-sm font-semibold text-slate-200 mb-2.5 flex items-center gap-1.5">
                    <Users2 className="w-4 h-4 text-fuchsia-400" />
                    المجموعات التي نشر فيها
                  </h3>
                  {data.groups.length === 0 ? (
                    <p className="text-xs text-slate-500 text-center py-3">
                      لا روابط لهذا المرسل داخل النافذة المحددة.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {data.groups.slice(0, 10).map((g, i) => {
                        const widthPct = Math.max(3, Math.round((g.total / maxGroup) * 100))
                        const gWaPct = g.total ? Math.round((g.whatsapp / g.total) * widthPct) : 0
                        const gTgPct = widthPct - gWaPct
                        return (
                          <div
                            key={`${g.group}-${i}`}
                            className={onOpenGroup
                              ? 'group/g rounded-md p-1 -mx-1 cursor-pointer hover:bg-slate-800/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-500/50'
                              : 'group/g'}
                            onClick={onOpenGroup ? () => onOpenGroup(g.group) : undefined}
                            role={onOpenGroup ? 'button' : undefined}
                            tabIndex={onOpenGroup ? 0 : undefined}
                            aria-label={onOpenGroup ? `عرض تفاصيل ${g.group}` : undefined}
                            onKeyDown={(e) => {
                              if (onOpenGroup && (e.key === 'Enter' || e.key === ' ')) {
                                e.preventDefault()
                                onOpenGroup(g.group)
                              }
                            }}
                          >
                            <div className="flex items-center justify-between gap-2 mb-1">
                              <div className="flex items-center gap-2 min-w-0">
                                <span className="text-[10px] text-slate-500 w-5 text-center flex-shrink-0 tabular-nums">
                                  {i + 1}.
                                </span>
                                <span
                                  className={`text-xs truncate ${g.group === 'غير محدد' ? 'text-slate-500 italic' : 'text-slate-200'}`}
                                  title={g.group}
                                >
                                  {g.group}
                                </span>
                              </div>
                              <div className="flex items-center gap-2 flex-shrink-0">
                                <span className="text-[10px] text-slate-500 tabular-nums">
                                  {g.share.toFixed(1)}%
                                </span>
                                <span className="text-xs font-bold text-white tabular-nums">
                                  {g.total.toLocaleString()}
                                </span>
                              </div>
                            </div>
                            <div className="flex h-1.5 rounded-full bg-slate-700/40 overflow-hidden">
                              <div
                                className="h-full bg-gradient-to-r from-emerald-400 to-emerald-600"
                                style={{ width: `${gWaPct}%` }}
                              />
                              <div
                                className="h-full bg-gradient-to-r from-blue-400 to-blue-600"
                                style={{ width: `${gTgPct}%` }}
                              />
                            </div>
                            <div className="text-[9px] text-slate-600 mt-0.5 h-3 opacity-0 group-hover/g:opacity-100 transition-opacity flex items-center justify-between gap-2">
                              <span className="truncate">
                                واتساب {g.whatsapp.toLocaleString()} · تيليجرام {g.telegram.toLocaleString()}
                                {g.other > 0 && <span> · أخرى {g.other.toLocaleString()}</span>}
                                <span> · نشاط {g.first_seen} ← {g.last_seen}</span>
                              </span>
                              {onOpenGroup && (
                                <span
                                  className="flex items-center gap-0.5 text-slate-500 group-hover/g:text-fuchsia-400 flex-shrink-0 transition-colors"
                                  aria-hidden
                                >
                                  <ZoomIn className="w-2.5 h-2.5" />
                                  تفاصيل
                                </span>
                              )}
                            </div>
                          </div>
                        )
                      })}
                      {data.groups.length > 10 && (
                        <p className="text-[10px] text-slate-500 text-center">
                          + {data.groups.length - 10} مجموعات أخرى
                        </p>
                      )}
                    </div>
                  )}
                </div>

                <p className="text-[10px] text-slate-600 text-center border-t border-slate-800 pt-3">
                  🔒 بدون بيانات هواتف أو جهات اتصال (خصوصية) · اضغط على أي مجموعة لعرض تفاصيلها
                </p>
              </div>
            ) : null}
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
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
  topSenders: TopSender[]
  topSendersTotals: TopSendersTotals | null
  trendDays: number
  onOpenGroupDetail?: (group: string) => void
  // [SENDER-DRILL] open the per-sender drill-down from the top-senders
  // view-all modal (same swap pattern as onOpenGroupDetail).
  onOpenSenderDetail?: (sender: string) => void
}) {
  const { type, onClose, allLinks, joiners, joinersSummary, joinedGroups, monitoredChats, monitoredSummary, bannedGroups, pendingApprovals, pendingSummary, topGroups, topGroupsTotals, topSenders, topSendersTotals, trendDays, onOpenGroupDetail, onOpenSenderDetail } = props
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
      case 'top_senders':
        return `👤 أكثر المرسلين نشراً (آخر ${trendDays} يوم)`
      default:
        return ''
    }
  }, [type, trendDays])

  // [A11Y] dialog semantics + focus trap + scroll-lock for this modal
  const a11y = useModalA11y(!!type, modalTitle || 'نافذة تفاصيل')

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
          ref={a11y.ref}
          {...a11y.dialogProps}
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="bg-slate-900 border border-slate-700 rounded-xl max-w-5xl w-full max-h-[95vh] overflow-hidden flex flex-col focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/60"
          onClick={(e: ReactMouseEvent) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-slate-700">
            <h2 className="text-xl font-bold text-white">{modalTitle}</h2>
            <div className="flex items-center gap-3">
              {type !== 'joiners' && type !== 'pending_approvals' && type !== 'top_groups' && type !== 'top_senders' && (
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
            <div className="p-4 overflow-y-auto flex-1 modal-scroll">
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
            <div className="flex-1 overflow-y-auto p-4 modal-scroll" style={{ maxHeight: 'calc(95vh - 100px)' }}>
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
            <div className="flex-1 overflow-y-auto p-4 modal-scroll" style={{ maxHeight: 'calc(95vh - 100px)' }}>
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
            <div className="flex-1 overflow-y-auto p-4 modal-scroll" style={{ maxHeight: 'calc(95vh - 100px)' }}>
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
            <div className="flex-1 overflow-y-auto p-4 modal-scroll" style={{ maxHeight: 'calc(95vh - 100px)' }}>
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
            <div className="flex-1 overflow-y-auto p-4 modal-scroll" style={{ maxHeight: 'calc(95vh - 100px)' }}>
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
                      className={`bg-slate-800/50 rounded-lg p-3 border border-slate-700/50 ${onOpenGroupDetail ? 'cursor-pointer hover:border-emerald-500/40 hover:bg-slate-800/80 transition-colors' : ''}`}
                      onClick={onOpenGroupDetail ? () => onOpenGroupDetail(g.group) : undefined}
                      role={onOpenGroupDetail ? 'button' : undefined}
                      tabIndex={onOpenGroupDetail ? 0 : undefined}
                      aria-label={onOpenGroupDetail ? `عرض تفاصيل ${g.group}` : undefined}
                      onKeyDown={(e) => {
                        if (onOpenGroupDetail && (e.key === 'Enter' || e.key === ' ')) {
                          e.preventDefault()
                          onOpenGroupDetail(g.group)
                        }
                      }}
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
          ) : type === 'top_senders' ? (
            <div className="flex-1 overflow-y-auto p-4 modal-scroll" style={{ maxHeight: 'calc(95vh - 100px)' }}>
              {topSendersTotals && (
                <div className="bg-teal-500/5 border border-teal-500/20 rounded-lg p-3 mb-4 text-xs text-teal-300/90 flex items-start gap-2">
                  <UserCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  <p>
                    نشر{' '}
                    <span className="font-bold text-white">
                      {topSendersTotals.total.toLocaleString()}
                    </span>{' '}
                    رابط{' '}
                    <span className="font-bold text-white">
                      {topSendersTotals.distinct_senders.toLocaleString()}
                    </span>{' '}
                    مرسل خلال آخر {trendDays} يوم. القائمة بدون أرقام هواتف (خصوصية).
                  </p>
                </div>
              )}
              {topSendersTotals && topSendersTotals.distinct_senders > topSenders.length && (
                <p className="text-[10px] text-slate-500 mb-3">
                  * يتم عرض أعلى {topSenders.length} مرسل فقط من إجمالي{' '}
                  {topSendersTotals.distinct_senders.toLocaleString()} مرسل.
                </p>
              )}
              {topSenders.length === 0 ? (
                <div className="text-center py-20">
                  <UserCircle2 className="w-12 h-12 mx-auto text-slate-600 mb-4" />
                  <p className="text-slate-500">لا توجد بيانات مرسلين</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {topSenders.map((s, i) => (
                    <div
                      key={s.sender}
                      className={`bg-slate-800/50 rounded-lg p-3 border border-slate-700/50 ${onOpenSenderDetail ? 'cursor-pointer hover:border-teal-500/40 hover:bg-slate-800/80 transition-colors' : ''}`}
                      onClick={onOpenSenderDetail ? () => onOpenSenderDetail(s.sender) : undefined}
                      role={onOpenSenderDetail ? 'button' : undefined}
                      tabIndex={onOpenSenderDetail ? 0 : undefined}
                      aria-label={onOpenSenderDetail ? `عرض تفاصيل ${s.sender}` : undefined}
                      onKeyDown={(e) => {
                        if (onOpenSenderDetail && (e.key === 'Enter' || e.key === ' ')) {
                          e.preventDefault()
                          onOpenSenderDetail(s.sender)
                        }
                      }}
                    >
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-xs w-7 text-center flex-shrink-0 font-mono text-slate-500">
                            {i + 1}
                          </span>
                          <span
                            className={`text-sm truncate ${s.sender === 'غير محدد' ? 'text-slate-500 italic' : 'text-slate-200'}`}
                            title={s.sender}
                          >
                            {s.sender}
                          </span>
                          {s.groups_count > 1 && (
                            <span
                              className="text-[9px] px-1.5 py-0.5 rounded-full border border-teal-500/30 bg-teal-500/10 text-teal-300 whitespace-nowrap flex-shrink-0"
                              title={`نشر في ${s.groups_count} مجموعة مختلفة`}
                            >
                              {s.groups_count} مجموعة
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <Badge className="bg-slate-700/50 text-slate-300 border-slate-600/40 text-[10px] tabular-nums">
                            {s.share.toFixed(1)}%
                          </Badge>
                          <span className="text-sm font-bold text-white tabular-nums">
                            {s.total.toLocaleString()}
                          </span>
                        </div>
                      </div>
                      <div className="flex h-1.5 rounded-full bg-slate-700/40 overflow-hidden mb-2">
                        <div
                          className="h-full bg-gradient-to-r from-emerald-400 to-emerald-600"
                          style={{ width: `${s.total ? (s.whatsapp / s.total) * 100 : 0}%` }}
                        />
                        <div
                          className="h-full bg-gradient-to-r from-blue-400 to-blue-600"
                          style={{ width: `${s.total ? (s.telegram / s.total) * 100 : 0}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between text-[10px] text-slate-400 flex-wrap gap-1">
                        <span className="flex items-center gap-2">
                          <span className="text-emerald-500">
                            واتساب {s.whatsapp.toLocaleString()}
                          </span>
                          <span className="text-blue-400">
                            تيليجرام {s.telegram.toLocaleString()}
                          </span>
                          {s.other > 0 && <span>أخرى {s.other.toLocaleString()}</span>}
                        </span>
                        <span className="text-slate-500" dir="ltr">
                          {s.first_seen} → {s.last_seen}
                        </span>
                      </div>
                      <p className="text-[10px] text-slate-500 mt-1 truncate flex items-center justify-between gap-2" title={s.top_group}>
                        <span className="truncate">
                          الأكثر نشراً في: <span className="text-slate-400">{s.top_group}</span>
                        </span>
                        {onOpenSenderDetail && (
                          <span className="flex items-center gap-0.5 text-slate-500 flex-shrink-0" aria-hidden>
                            <ZoomIn className="w-3 h-3" />
                            تفاصيل
                          </span>
                        )}
                      </p>
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
              <div className="flex-1 overflow-y-auto p-4 modal-scroll" style={{ maxHeight: 'calc(95vh - 200px)' }}>
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
