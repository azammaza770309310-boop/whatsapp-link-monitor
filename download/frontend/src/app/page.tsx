'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import {
  MessageCircle, Send, Search, Users, Link2,
  RefreshCw, ExternalLink, Phone, MapPin, Clock, Globe, ArrowRight,
  X, Activity, CheckCircle2, XCircle, AlertTriangle, Clock3,
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

interface CountryStat {
  country: string
  count: number
  percentage: number
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
  | null

// ===== Constants =====
const API_URL: string =
  process.env.NEXT_PUBLIC_API_URL || 'https://whatsapp-userbot-yzm7.onrender.com'

const COUNTRY_KEYWORDS: Record<string, string[]> = {
  'السعودية': ['السعودية', 'saudi', 'ksa', 'السعودي', 'KAU', 'KSU', 'KFU', 'KFUPM', 'PSAU', 'UQU', 'IAU', 'SEU'],
  'الكويت': ['الكويت', 'kuwait', 'AUM', 'AUK', 'GUST'],
  'قطر': ['قطر', 'qatar', 'QU', 'HBKU'],
  'البحرين': ['البحرين', 'bahrain'],
  'الإمارات': ['الإمارات', 'UAE', 'Khalifa', 'Zayed', 'UAEU'],
}

function detectCountry(text: string): string | null {
  if (!text) return null
  const lower = text.toLowerCase()
  for (const [country, keywords] of Object.entries(COUNTRY_KEYWORDS)) {
    for (const kw of keywords) {
      if (lower.includes(kw.toLowerCase())) return country
    }
  }
  return null
}

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
  const [countryStats, setCountryStats] = useState<CountryStat[]>([])
  const [joiners, setJoiners] = useState<Joiner[]>([])
  const [joinersSummary, setJoinersSummary] = useState<JoinersSummary | null>(null)
  const [joinedGroups, setJoinedGroups] = useState<JoinedGroup[]>([])
  const [monitoredChats, setMonitoredChats] = useState<MonitoredChat[]>([])
  const [monitoredSummary, setMonitoredSummary] = useState<MonitoredSummary | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [modal, setModal] = useState<ModalType>(null)

  // ===== Fetch Functions =====
  const fetchStats = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/stats`, {
        headers: { Accept: 'application/json' },
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
      }
    } catch {
      // silent
    }
  }, [])

  const fetchLinks = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/links?limit=5000`, {
        headers: { Accept: 'application/json' },
      })
      if (response.ok) {
        const data = await response.json()
        const links: LinkItem[] = data.links || []
        if (Array.isArray(links)) {
          setAllLinks(links)
          setLoading(false)
        }
      }
    } catch {
      // silent
    }
  }, [])

  const fetchJoiners = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/joiners_status`, {
        headers: { Accept: 'application/json' },
      })
      if (response.ok) {
        const data = await response.json()
        setJoiners(data.joiners || [])
        setJoinersSummary(data.summary || null)
        setJoinedGroups(data.joined_groups || [])
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
        headers: { Accept: 'application/json' },
      })
      if (response.ok) {
        const data = await response.json()
        setMonitoredChats(data.chats || [])
        setMonitoredSummary(data.summary || null)
      } else {
        console.error('fetchMonitoredChats HTTP error:', response.status)
      }
    } catch (err) {
      console.error('fetchMonitoredChats error:', err)
    }
  }, [])

  // Calculate country stats from links
  useEffect(() => {
    if (allLinks.length === 0) return
    const counts: Record<string, number> = {}
    let total = 0
    for (const item of allLinks) {
      const text = `${item.message_text || ''} ${item.group_name || ''} ${item.ai_country || ''}`
      const country = item.ai_country || detectCountry(text)
      if (country) {
        counts[country] = (counts[country] || 0) + 1
        total++
      }
    }
    const result: CountryStat[] = Object.entries(counts)
      .map(([country, count]) => ({
        country,
        count,
        percentage: total > 0 ? Math.round((count / total) * 100) : 0,
      }))
      .sort((a, b) => b.count - a.count)
    setCountryStats(result)
  }, [allLinks])

  // Initial load + auto refresh (real-time every 15s)
  useEffect(() => {
    const load = async () => {
      await Promise.all([fetchLinks(), fetchStats(), fetchJoiners(), fetchMonitoredChats()])
    }
    load()
    const interval = setInterval(load, 15000) // 15s for real-time updates
    return () => clearInterval(interval)
  }, [fetchLinks, fetchStats, fetchJoiners, fetchMonitoredChats])

  const refreshAll = useCallback(() => {
    fetchLinks()
    fetchStats()
    fetchJoiners()
    fetchMonitoredChats()
  }, [fetchLinks, fetchStats, fetchJoiners, fetchMonitoredChats])

  const countryColors: Record<string, string> = {
    'السعودية': 'from-emerald-500 to-green-400',
    'الكويت': 'from-blue-500 to-cyan-400',
    'قطر': 'from-purple-500 to-pink-400',
    'البحرين': 'from-amber-500 to-orange-400',
    'الإمارات': 'from-rose-500 to-red-400',
  }

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
            <Button
              variant="ghost"
              size="sm"
              onClick={refreshAll}
              className="text-slate-400 hover:text-white"
            >
              <RefreshCw className="w-4 h-4" />
            </Button>
          </div>
        </motion.div>

        {/* Main Stats Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4 mb-6">
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
        </div>

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
            </CardContent>
          </Card>
        )}

        {/* Country Stats */}
        {countryStats.length > 0 && (
          <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg">
                <Globe className="w-5 h-5 text-emerald-400" />
                التحليل الإحصائي حسب الدولة
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                {countryStats.map((cs) => (
                  <button
                    key={cs.country}
                    onClick={() => setModal('all_links')}
                    className="text-right hover:scale-105 transition-transform"
                  >
                    <Card
                      className={`bg-gradient-to-br ${
                        countryColors[cs.country] || 'from-slate-600 to-slate-700'
                      } border-0 overflow-hidden`}
                    >
                      <CardContent className="p-4">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-sm font-bold text-white">{cs.country}</span>
                          <Badge className="bg-black/30 text-white border-0">{cs.count}</Badge>
                        </div>
                        <div className="w-full bg-black/30 rounded-full h-2 overflow-hidden">
                          <div
                            className="h-full bg-white/80 rounded-full"
                            style={{ width: `${cs.percentage}%` }}
                          />
                        </div>
                        <span className="text-xs text-white/80 mt-1 block">{cs.percentage}%</span>
                      </CardContent>
                    </Card>
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Monitored Chats Section — المجموعات المراقبة */}
        <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
          <CardHeader>
            <CardTitle className="flex items-center justify-between text-lg">
              <span className="flex items-center gap-2">
                <span className="text-2xl">👁️</span>
                المجموعات المراقبة
              </span>
              {monitoredSummary && (
                <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/40">
                  {monitoredSummary.total} مجموعة
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {/* Stats Row */}
            <div className="grid grid-cols-4 gap-3 mb-4">
              <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 text-center">
                <p className="text-2xl font-bold text-blue-400">
                  {monitoredSummary?.total ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-1">إجمالي المراقبة</p>
              </div>
              <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
                <p className="text-2xl font-bold text-emerald-400">
                  {monitoredSummary?.classified ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-1">مُصنّفة بـ AI</p>
              </div>
              <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center">
                <p className="text-2xl font-bold text-amber-400">
                  {monitoredSummary?.educational ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-1">تعليمية</p>
              </div>
              <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-3 text-center">
                <p className="text-2xl font-bold text-purple-400">
                  {monitoredSummary?.high_relevance ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-1">صلة عالية</p>
              </div>
            </div>

            {/* Recent Monitored Chats Preview */}
            <button
              onClick={() => setModal('monitored_chats')}
              className="w-full text-right hover:scale-[1.01] transition-transform"
            >
              <div className="bg-slate-700/30 rounded-lg p-3 border border-slate-700">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-sm font-semibold text-slate-300">
                    📋 أحدث المجموعات المراقبة
                  </h4>
                  <ArrowRight className="w-4 h-4 text-slate-400" />
                </div>
                {monitoredChats.length > 0 ? (
                  <div className="space-y-1">
                    {monitoredChats.slice(0, 3).map((c) => (
                      <div
                        key={c.chat_id}
                        className="bg-slate-900/50 rounded p-2 text-xs flex items-center justify-between"
                      >
                        <span className="text-white truncate flex-1">
                          {c.chat_title || 'غير معروف'}
                        </span>
                        <div className="flex items-center gap-2 mr-2">
                          {c.ai_classification && c.ai_classification !== 'unknown' && (
                            <Badge variant="outline" className="text-[10px] px-1 py-0">
                              {c.ai_classification === 'group' ? '👥' : c.ai_classification === 'channel' ? '📢' : '?'}
                            </Badge>
                          )}
                          <span className="text-slate-400">
                            {c.ai_relevance > 0 ? `${c.ai_relevance}%` : ''}
                          </span>
                          {c.ai_country && c.ai_country !== 'أخرى' && (
                            <span className="text-purple-400 text-[10px]">{c.ai_country}</span>
                          )}
                        </div>
                      </div>
                    ))}
                    {monitoredChats.length > 3 && (
                      <p className="text-xs text-slate-500 text-center pt-1">
                        + {monitoredChats.length - 3} مجموعة أخرى...
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
              <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center">
                <p className="text-2xl font-bold text-red-400">
                  {joinersSummary?.total_banned ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-1">مجموعات ممنوعة</p>
              </div>
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
                  <LinkCard key={link.id} link={link} compact />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <footer className="mt-10 text-center text-slate-500 text-xs">
          <p>نظام مراقبة الروابط © 2026</p>
        </footer>
      </div>

      {/* Modal */}
      <LinksModal
        type={modal}
        onClose={() => setModal(null)}
        allLinks={allLinks}
        joiners={joiners}
        joinersSummary={joinersSummary}
      />
    </div>
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
  return (
    <button
      onClick={onClick}
      className="text-right hover:scale-105 transition-transform w-full"
    >
      <Card
        className={`bg-gradient-to-br ${gradient} border-slate-700/50 backdrop-blur-sm overflow-hidden`}
      >
        <CardContent className="p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-slate-400 text-xs mb-1">{label}</p>
              <p className="text-2xl font-bold text-white">{value.toLocaleString()}</p>
            </div>
            <div className={`${iconColor} opacity-80`}>{icon}</div>
          </div>
        </CardContent>
      </Card>
    </button>
  )
}

// ===== LinkCard Component =====
function LinkCard(props: { link: LinkItem; compact?: boolean }) {
  const { link, compact = false } = props
  const isWhatsapp = link.link_type === 'whatsapp'
  const date = new Date(link.created_at)
  const timeStr = date.toLocaleString('ar-SA', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
  const country =
    link.ai_country || detectCountry(`${link.message_text || ''} ${link.group_name || ''}`)
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
          </span>
          <span className="text-xs text-slate-500">{timeStr}</span>
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
          </div>
          <div className="flex items-center gap-1 text-slate-500 text-xs">
            <Clock className="w-3 h-3" />
            {timeStr}
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
}) {
  const { type, onClose, allLinks, joiners, joinersSummary } = props
  const [searchQuery, setSearchQuery] = useState<string>('')

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
      default:
        return ''
    }
  }, [type])

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
              {type !== 'joiners' && (
                <Badge className="bg-slate-700 text-white border-0">
                  {filteredLinks.length}
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
          ) : (
            <div className="flex flex-col flex-1 overflow-hidden">
              {/* Search */}
              <div className="p-4 border-b border-slate-700">
                <div className="relative">
                  <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <Input
                    placeholder="ابحث في الروابط..."
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
                      <LinkCard key={link.id} link={link} />
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
