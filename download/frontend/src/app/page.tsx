'use client'

import { useState, useEffect, useCallback, useMemo, createElement, ReactNode } from 'react'
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
  X, Activity, CheckCircle2, XCircle, AlertTriangle, Clock3
} from 'lucide-react'

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

type ModalType = 'whatsapp' | 'telegram' | 'all_links' | 'ai_approved' | 'ai_rejected' | 'ai_ads' | 'joiners' | null

// ===== Constants =====
const API_URL: string = process.env.NEXT_PUBLIC_API_URL || 'https://whatsapp-userbot-yzm7.onrender.com'

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
export default function Home(): JSX.Element {
  const [allLinks, setAllLinks] = useState<LinkItem[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [countryStats, setCountryStats] = useState<CountryStat[]>([])
  const [joiners, setJoiners] = useState<Joiner[]>([])
  const [joinersSummary, setJoinersSummary] = useState<JoinersSummary | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [modal, setModal] = useState<ModalType>(null)

  // ===== Fetch Functions =====
  const fetchStats = useCallback(async (): Promise<void> => {
    try {
      const response = await fetch(`${API_URL}/api/stats`, {
        headers: { 'Accept': 'application/json' }
      })
      if (response.ok) {
        const data = await response.json()
        setStats({
          total_links: data.total_links || 0,
          whatsapp_links: data.whatsapp_links || 0,
          telegram_links: data.telegram_links || 0,
          active_watchers: data.active_watchers || 0,
          ai_stats: data.ai_stats ? {
            ai_approved: data.ai_stats.ai_approved || 0,
            ai_rejected: data.ai_stats.ai_rejected || 0,
            ai_ads: data.ai_stats.ai_ads || 0,
            ai_pending: data.ai_stats.ai_pending || 0,
            ai_batch_mode: !!data.ai_stats.ai_batch_mode,
          } : undefined
        })
      }
    } catch {
      // silent
    }
  }, [])

  const fetchLinks = useCallback(async (): Promise<void> => {
    try {
      const response = await fetch(`${API_URL}/api/links?limit=500`, {
        headers: { 'Accept': 'application/json' }
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

  const fetchJoiners = useCallback(async (): Promise<void> => {
    try {
      const response = await fetch(`${API_URL}/api/joiners_status`, {
        headers: { 'Accept': 'application/json' }
      })
      if (response.ok) {
        const data = await response.json()
        setJoiners(data.joiners || [])
        setJoinersSummary(data.summary || null)
      }
    } catch {
      // silent
    }
  }, [])

  // Calculate country stats from links
  useEffect((): void => {
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
    const result: CountryStat[] = Object.entries(counts).map(([country, count]) => ({
      country, count,
      percentage: total > 0 ? Math.round((count / total) * 100) : 0
    })).sort((a, b) => b.count - a.count)
    setCountryStats(result)
  }, [allLinks])

  // Initial load + auto refresh
  useEffect((): (() => void) => {
    const load = async (): Promise<void> => {
      await Promise.all([fetchLinks(), fetchStats(), fetchJoiners()])
    }
    load()
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [fetchLinks, fetchStats, fetchJoiners])

  const refreshAll = useCallback((): void => {
    fetchLinks()
    fetchStats()
    fetchJoiners()
  }, [fetchLinks, fetchStats, fetchJoiners])

  const countryColors: Record<string, string> = {
    'السعودية': 'from-emerald-500 to-green-400',
    'الكويت': 'from-blue-500 to-cyan-400',
    'قطر': 'from-purple-500 to-pink-400',
    'البحرين': 'from-amber-500 to-orange-400',
    'الإمارات': 'from-rose-500 to-red-400'
  }

  // Build elements using createElement (no JSX)
  const headerElement = createElement(
    motion.div,
    { initial: { opacity: 0, y: -20 }, animate: { opacity: 1, y: 0 }, className: 'mb-8' },
    createElement(
      'div',
      { className: 'flex items-center justify-between' },
      createElement(
        'div',
        { className: 'flex items-center gap-3' },
        createElement(
          'div',
          { className: 'w-12 h-12 rounded-2xl bg-gradient-to-br from-emerald-500 to-blue-500 flex items-center justify-center shadow-lg shadow-emerald-500/20' },
          createElement(Link2, { className: 'w-6 h-6 text-white' })
        ),
        createElement(
          'div',
          null,
          createElement('h1', { className: 'text-2xl md:text-3xl font-bold bg-gradient-to-r from-emerald-400 via-white to-blue-400 bg-clip-text text-transparent' }, 'مراقب الروابط'),
          createElement('p', { className: 'text-slate-400 text-xs' }, 'نظام سحب الروابط الذكي')
        )
      ),
      createElement(
        Button,
        { variant: 'ghost', size: 'sm', onClick: refreshAll, className: 'text-slate-400 hover:text-white' },
        createElement(RefreshCw, { className: 'w-4 h-4' })
      )
    )
  )

  const statsCards = createElement(
    'div',
    { className: 'grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4 mb-6' },
    createElement(StatCard, {
      icon: createElement(Link2, { className: 'w-5 h-5' }),
      label: 'إجمالي الروابط',
      value: stats?.total_links ?? 0,
      gradient: 'from-emerald-500/20 to-emerald-500/5',
      iconColor: 'text-emerald-400',
      onClick: () => setModal('all_links')
    }),
    createElement(StatCard, {
      icon: createElement(MessageCircle, { className: 'w-5 h-5' }),
      label: '🟢 واتساب',
      value: stats?.whatsapp_links ?? 0,
      gradient: 'from-green-500/20 to-green-500/5',
      iconColor: 'text-green-400',
      onClick: () => setModal('whatsapp')
    }),
    createElement(StatCard, {
      icon: createElement(Send, { className: 'w-5 h-5' }),
      label: '🔵 تيليجرام',
      value: stats?.telegram_links ?? 0,
      gradient: 'from-blue-500/20 to-blue-500/5',
      iconColor: 'text-blue-400',
      onClick: () => setModal('telegram')
    }),
    createElement(StatCard, {
      icon: createElement(Users, { className: 'w-5 h-5' }),
      label: 'المراقبون',
      value: stats?.active_watchers ?? 0,
      gradient: 'from-purple-500/20 to-purple-500/5',
      iconColor: 'text-purple-400',
      onClick: () => setModal('joiners')
    })
  )

  // Build AI stats section
  const aiStatsSection = stats?.ai_stats ? createElement(
    Card,
    { className: 'bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6' },
    createElement(
      CardHeader,
      null,
      createElement(
        CardTitle,
        { className: 'flex items-center justify-between text-lg' },
        createElement('span', { className: 'flex items-center gap-2' },
          createElement('span', { className: 'text-2xl' }, '🤖'),
          'تحليل الذكاء الاصطناعي'
        ),
        createElement(Badge, {
          variant: 'outline',
          className: stats.ai_stats.ai_batch_mode
            ? 'border-amber-500/40 text-amber-400 bg-amber-500/10'
            : 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
        }, stats.ai_stats.ai_batch_mode ? '⏭️ Batch Mode' : '🤖 AI Active')
      )
    ),
    createElement(
      CardContent,
      null,
      createElement(
        'div',
        { className: 'grid grid-cols-2 md:grid-cols-4 gap-3' },
        createElement('button', { onClick: () => setModal('ai_approved'), className: 'text-right hover:scale-105 transition-transform' },
          createElement('div', { className: 'bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center' },
            createElement(CheckCircle2, { className: 'w-5 h-5 text-emerald-400 mx-auto mb-1' }),
            createElement('p', { className: 'text-2xl font-bold text-emerald-400' }, stats.ai_stats.ai_approved),
            createElement('p', { className: 'text-xs text-slate-400 mt-1' }, '✅ موافق عليه')
          )
        ),
        createElement('button', { onClick: () => setModal('ai_rejected'), className: 'text-right hover:scale-105 transition-transform' },
          createElement('div', { className: 'bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center' },
            createElement(XCircle, { className: 'w-5 h-5 text-red-400 mx-auto mb-1' }),
            createElement('p', { className: 'text-2xl font-bold text-red-400' }, stats.ai_stats.ai_rejected),
            createElement('p', { className: 'text-xs text-slate-400 mt-1' }, '❌ مرفوض')
          )
        ),
        createElement('button', { onClick: () => setModal('ai_ads'), className: 'text-right hover:scale-105 transition-transform' },
          createElement('div', { className: 'bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center' },
            createElement(AlertTriangle, { className: 'w-5 h-5 text-amber-400 mx-auto mb-1' }),
            createElement('p', { className: 'text-2xl font-bold text-amber-400' }, stats.ai_stats.ai_ads),
            createElement('p', { className: 'text-xs text-slate-400 mt-1' }, '⚠️ إعلان')
          )
        ),
        createElement('div', { className: 'bg-slate-700/30 border border-slate-700 rounded-lg p-3 text-center' },
          createElement(Clock3, { className: 'w-5 h-5 text-slate-300 mx-auto mb-1' }),
          createElement('p', { className: 'text-2xl font-bold text-slate-300' }, stats.ai_stats.ai_pending),
          createElement('p', { className: 'text-xs text-slate-400 mt-1' }, '⏳ لم يُفحص')
        )
      )
    )
  ) : null

  // Country stats section
  const countrySection = countryStats.length > 0 ? createElement(
    Card,
    { className: 'bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6' },
    createElement(
      CardHeader,
      null,
      createElement(CardTitle, { className: 'flex items-center gap-2 text-lg' },
        createElement(Globe, { className: 'w-5 h-5 text-emerald-400' }),
        'التحليل الإحصائي حسب الدولة'
      )
    ),
    createElement(
      CardContent,
      null,
      createElement(
        'div',
        { className: 'grid grid-cols-2 md:grid-cols-3 gap-3' },
        ...countryStats.map((cs) => createElement('button', {
          key: cs.country,
          className: 'text-right hover:scale-105 transition-transform'
        },
          createElement(Card, { className: `bg-gradient-to-br ${countryColors[cs.country] || 'from-slate-600 to-slate-700'} border-0 overflow-hidden` },
            createElement(CardContent, { className: 'p-4' },
              createElement('div', { className: 'flex items-center justify-between mb-2' },
                createElement('span', { className: 'text-sm font-bold text-white' }, cs.country),
                createElement(Badge, { className: 'bg-black/30 text-white border-0' }, cs.count)
              ),
              createElement('div', { className: 'w-full bg-black/30 rounded-full h-2 overflow-hidden' },
                createElement('div', { className: 'h-full bg-white/80 rounded-full', style: { width: `${cs.percentage}%` } })
              ),
              createElement('span', { className: 'text-xs text-white/80 mt-1 block' }, `${cs.percentage}%`)
            )
          )
        ))
      )
    )
  ) : null

  // Joiner dashboard section
  const joinerSection = createElement(
    Card,
    { className: 'bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6' },
    createElement(
      CardHeader,
      null,
      createElement(
        CardTitle,
        { className: 'flex items-center justify-between text-lg' },
        createElement('span', { className: 'flex items-center gap-2' },
          createElement('span', { className: 'text-2xl' }, '🚀'),
          'لوحة الفدائي'
        ),
        joinersSummary ? createElement(Badge, { className: 'bg-purple-500/20 text-purple-400 border-purple-500/40' },
          `${joinersSummary.connected_joiners}/${joinersSummary.total_joiners} متصل`
        ) : null
      )
    ),
    createElement(
      CardContent,
      null,
      createElement('div', { className: 'grid grid-cols-3 gap-3 mb-4' },
        createElement('div', { className: 'bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center' },
          createElement('p', { className: 'text-2xl font-bold text-emerald-400' }, joinersSummary?.total_joined_groups ?? 0),
          createElement('p', { className: 'text-xs text-slate-400 mt-1' }, 'مجموعة منضم إليها')
        ),
        createElement('div', { className: 'bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center' },
          createElement('p', { className: 'text-2xl font-bold text-amber-400' }, joinersSummary?.total_already_member ?? 0),
          createElement('p', { className: 'text-xs text-slate-400 mt-1' }, 'منضم مسبقاً')
        ),
        createElement('div', { className: 'bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center' },
          createElement('p', { className: 'text-2xl font-bold text-red-400' }, joinersSummary?.total_banned ?? 0),
          createElement('p', { className: 'text-xs text-slate-400 mt-1' }, 'مجموعات ممنوعة')
        )
      ),
      joiners.length > 0 ? createElement(
        'div',
        null,
        createElement('h4', { className: 'text-sm font-semibold text-slate-300 mb-2' }, 'حسابات الفدائيين:'),
        createElement(
          'div',
          { className: 'grid grid-cols-1 md:grid-cols-2 gap-2' },
          ...joiners.map((j) => createElement(
            'div',
            { key: j.phone, className: 'bg-slate-700/30 rounded-lg p-3 border border-slate-700' },
            createElement('div', { className: 'flex items-center justify-between mb-1' },
              createElement('span', { className: 'text-white font-mono text-xs' }, j.phone),
              createElement('span', { className: `text-xs px-2 py-0.5 rounded ${j.connected ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}` },
                j.connected ? '✅ متصل' : '❌ غير متصل'
              )
            ),
            createElement('div', { className: 'text-xs text-slate-400 flex justify-between' },
              createElement('span', null, 'انضمامات اليوم: ',
                createElement('span', { className: 'text-blue-400' }, `${j.daily_joins}/${j.daily_limit}`)
              ),
              j.last_join_timestamp ? createElement('span', null,
                `آخر: ${new Date(j.last_join_timestamp).toLocaleString('ar-SA', {hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short'})}`
              ) : null
            )
          ))
        )
      ) : createElement('div', { className: 'text-center py-8' },
        createElement('p', { className: 'text-slate-500 text-sm' }, 'جارٍ تحميل بيانات الفدائيين...')
      )
    )
  )

  // Recent links section
  const recentLinksSection = createElement(
    Card,
    { className: 'bg-slate-800/30 border-slate-700/50 backdrop-blur-sm' },
    createElement(
      CardHeader,
      null,
      createElement(
        CardTitle,
        { className: 'flex items-center justify-between text-lg' },
        createElement('span', { className: 'flex items-center gap-2' },
          createElement(Activity, { className: 'w-5 h-5 text-emerald-400' }),
          'أحدث الروابط'
        ),
        createElement(Button, { variant: 'ghost', size: 'sm', onClick: () => setModal('all_links'), className: 'text-slate-400 hover:text-white text-xs' },
          'عرض الكل', createElement(ArrowRight, { className: 'w-3 h-3 mr-1' })
        )
      )
    ),
    createElement(
      CardContent,
      null,
      loading ? createElement('div', { className: 'space-y-3' },
        ...[...Array(3)].map((_, i) => createElement(Skeleton, { key: i, className: 'h-20 w-full bg-slate-800/50 rounded-xl' }))
      ) : allLinks.length === 0 ? createElement('div', { className: 'text-center py-8' },
        createElement(Link2, { className: 'w-10 h-10 mx-auto text-slate-600 mb-2' }),
        createElement('p', { className: 'text-slate-500 text-sm' }, 'لا توجد روابط')
      ) : createElement('div', { className: 'space-y-2' },
        ...allLinks.slice(0, 5).map((link) => createElement(LinkCard, { key: link.id, link, compact: true }))
      )
    )
  )

  return createElement(
    'div',
    { className: 'min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white' },
    // Background
    createElement('div', { className: 'fixed inset-0 overflow-hidden pointer-events-none' },
      createElement('div', { className: 'absolute -top-40 -right-40 w-96 h-96 bg-emerald-500/10 rounded-full blur-3xl' }),
      createElement('div', { className: 'absolute -bottom-40 -left-40 w-96 h-96 bg-blue-500/10 rounded-full blur-3xl' })
    ),
    // Main content
    createElement(
      'div',
      { className: 'relative z-10 container mx-auto px-4 py-8 max-w-6xl' },
      headerElement,
      statsCards,
      aiStatsSection,
      countrySection,
      joinerSection,
      recentLinksSection,
      createElement('footer', { className: 'mt-10 text-center text-slate-500 text-xs' },
        createElement('p', null, 'نظام مراقبة الروابط © 2026')
      )
    ),
    // Modal
    createElement(LinksModal, {
      type: modal,
      onClose: () => setModal(null),
      allLinks,
      joiners,
      joinersSummary
    })
  )
}

// ===== StatCard Component =====
function StatCard(props: { icon: ReactNode; label: string; value: number; gradient: string; iconColor: string; onClick: () => void }): JSX.Element {
  const { icon, label, value, gradient, iconColor, onClick } = props
  return createElement(
    'button',
    { onClick, className: 'text-right hover:scale-105 transition-transform w-full' },
    createElement(Card, { className: `bg-gradient-to-br ${gradient} border-slate-700/50 backdrop-blur-sm overflow-hidden` },
      createElement(CardContent, { className: 'p-4' },
        createElement('div', { className: 'flex items-center justify-between' },
          createElement('div', null,
            createElement('p', { className: 'text-slate-400 text-xs mb-1' }, label),
            createElement('p', { className: 'text-2xl font-bold text-white' }, value.toLocaleString())
          ),
          createElement('div', { className: `${iconColor} opacity-80` }, icon)
        )
      )
    )
  )
}

// ===== LinkCard Component =====
function LinkCard(props: { link: LinkItem; compact?: boolean }): JSX.Element {
  const { link, compact = false } = props
  const isWhatsapp = link.link_type === 'whatsapp'
  const date = new Date(link.created_at)
  const timeStr = date.toLocaleString('ar-SA', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  const country = link.ai_country || detectCountry(`${link.message_text || ''} ${link.group_name || ''}`)
  const href = safeUrl(link.link)

  if (compact) {
    return createElement('div', { className: 'bg-slate-700/30 rounded-lg p-3 border border-slate-700/50 hover:bg-slate-700/50 transition-colors' },
      createElement('div', { className: 'flex items-center gap-2 mb-1' },
        createElement('div', { className: `w-2 h-2 rounded-full ${isWhatsapp ? 'bg-green-500' : 'bg-blue-500'}` }),
        createElement('span', { className: 'text-xs text-slate-300 truncate flex-1' }, link.group_name || 'غير معروف'),
        createElement('span', { className: 'text-xs text-slate-500' }, timeStr)
      ),
      href ? createElement('a', { href, target: '_blank', rel: 'noopener noreferrer', className: 'text-xs text-blue-400 hover:text-blue-300 truncate block font-mono' }, href) : null
    )
  }

  return createElement(
    Card,
    { className: 'mb-3 bg-slate-800/30 border-slate-700/50 backdrop-blur-sm hover:bg-slate-800/50 transition-all duration-300 overflow-hidden' },
    createElement(CardContent, { className: 'p-4' },
      createElement('div', { className: 'flex items-start justify-between mb-3' },
        createElement('div', { className: 'flex items-center gap-2 flex-wrap' },
          createElement('div', { className: `w-3 h-3 rounded-full ${isWhatsapp ? 'bg-green-500' : 'bg-blue-500'} shadow-lg` }),
          createElement(Badge, { variant: 'outline', className: isWhatsapp ? 'border-green-500/30 text-green-400' : 'border-blue-500/30 text-blue-400' },
            isWhatsapp ? '🟢 واتساب' : '🔵 تيليجرام'
          ),
          country ? createElement(Badge, { variant: 'outline', className: 'border-purple-500/30 text-purple-400' }, country) : null,
          link.ai_approved === true ? createElement(Badge, { variant: 'outline', className: 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10' }, '✅ AI موافق') : null,
          link.ai_approved === false ? createElement(Badge, { variant: 'outline', className: 'border-red-500/40 text-red-400 bg-red-500/10' }, '❌ AI مرفوض') : null,
          link.ai_is_ad === true ? createElement(Badge, { variant: 'outline', className: 'border-amber-500/40 text-amber-400 bg-amber-500/10' }, '⚠️ إعلان') : null
        ),
        createElement('div', { className: 'flex items-center gap-1 text-slate-500 text-xs' },
          createElement(Clock, { className: 'w-3 h-3' }), timeStr
        )
      ),
      href ? createElement('a', { href, target: '_blank', rel: 'noopener noreferrer', className: 'block mb-3 group' },
        createElement('div', { className: 'flex items-center gap-2 bg-slate-900/50 rounded-lg p-3 hover:bg-slate-900/80 transition-colors' },
          createElement(ExternalLink, { className: 'w-4 h-4 text-slate-400 group-hover:text-white shrink-0' }),
          createElement('span', { className: 'text-sm text-blue-300 group-hover:text-blue-200 truncate font-mono' }, href)
        )
      ) : createElement('div', { className: 'mb-3 bg-slate-900/50 rounded-lg p-3' },
        createElement('div', { className: 'flex items-center gap-2' },
          createElement(ExternalLink, { className: 'w-4 h-4 text-slate-500 shrink-0' }),
          createElement('span', { className: 'text-sm text-slate-400 truncate font-mono' }, link.link || '(no URL)')
        )
      ),
      link.ai_description ? createElement('div', { className: 'mb-2 bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-2 text-xs text-emerald-300/90' },
        createElement('span', { className: 'font-semibold' }, '🤖 وصف AI:'), ' ', link.ai_description
      ) : null,
      createElement('div', { className: 'grid grid-cols-2 gap-2 text-xs' },
        link.group_name ? createElement('div', { className: 'flex items-center gap-1.5 text-slate-400' },
          createElement(MapPin, { className: 'w-3 h-3 shrink-0' }),
          createElement('span', { className: 'truncate' }, link.group_name)
        ) : null,
        link.sender_name ? createElement('div', { className: 'flex items-center gap-1.5 text-slate-400' },
          createElement(Users, { className: 'w-3 h-3 shrink-0' }),
          createElement('span', { className: 'truncate' }, link.sender_name)
        ) : null,
        link.sender_contact ? createElement('div', { className: 'flex items-center gap-1.5 text-slate-400' },
          createElement(Phone, { className: 'w-3 h-3 shrink-0' }),
          createElement('span', { className: 'truncate' }, link.sender_contact)
        ) : null
      ),
      link.message_text ? createElement('div', { className: 'mt-3 bg-slate-900/40 rounded-lg p-3 text-xs text-slate-400 max-h-24 overflow-hidden' }, link.message_text) : null
    )
  )
}

// ===== Modal Component =====
function LinksModal(props: { type: ModalType; onClose: () => void; allLinks: LinkItem[]; joiners: Joiner[]; joinersSummary: JoinersSummary | null }): JSX.Element | null {
  const { type, onClose, allLinks, joiners, joinersSummary } = props
  const [searchQuery, setSearchQuery] = useState<string>('')

  const filteredLinks = useMemo((): LinkItem[] => {
    if (!type) return []
    let filtered = [...allLinks]

    if (type === 'whatsapp') {
      filtered = filtered.filter(l => l.link_type === 'whatsapp')
    } else if (type === 'telegram') {
      filtered = filtered.filter(l => l.link_type === 'telegram')
    } else if (type === 'ai_approved') {
      filtered = filtered.filter(l => l.ai_approved === true)
    } else if (type === 'ai_rejected') {
      filtered = filtered.filter(l => l.ai_approved === false)
    } else if (type === 'ai_ads') {
      filtered = filtered.filter(l => l.ai_is_ad === true)
    } else if (type === 'all_links') {
      // Show all
    }

    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter(l =>
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
      case 'whatsapp': return '🟢 روابط واتساب'
      case 'telegram': return '🔵 روابط تيليجرام'
      case 'all_links': return '🔗 جميع الروابط'
      case 'ai_approved': return '✅ روابط موافق عليها (AI)'
      case 'ai_rejected': return '❌ روابط مرفوضة (AI)'
      case 'ai_ads': return '⚠️ روابط مُصنّفة كإعلانات (AI)'
      case 'joiners': return '🚀 لوحة الفدائي التفصيلية'
      default: return ''
    }
  }, [type])

  if (!type) return null

  return createElement(
    AnimatePresence,
    null,
    createElement(
      motion.div,
      {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        exit: { opacity: 0 },
        className: 'fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4',
        onClick: onClose
      },
      createElement(
        motion.div,
        {
          initial: { scale: 0.95, opacity: 0 },
          animate: { scale: 1, opacity: 1 },
          exit: { scale: 0.95, opacity: 0 },
          className: 'bg-slate-900 border border-slate-700 rounded-xl max-w-5xl w-full max-h-[95vh] overflow-hidden flex flex-col',
          onClick: (e: React.MouseEvent) => e.stopPropagation()
        },
        // Header
        createElement('div', { className: 'flex items-center justify-between p-4 border-b border-slate-700' },
          createElement('h2', { className: 'text-xl font-bold text-white' }, modalTitle),
          createElement('div', { className: 'flex items-center gap-3' },
            type !== 'joiners' ? createElement(Badge, { className: 'bg-slate-700 text-white border-0' }, filteredLinks.length) : null,
            createElement('button', { onClick: onClose, className: 'text-slate-400 hover:text-white transition-colors' },
              createElement(X, { className: 'w-5 h-5' })
            )
          )
        ),
        // Content
        type === 'joiners' ? createElement(
          'div',
          { className: 'p-4 overflow-y-auto flex-1' },
          joinersSummary ? createElement('div', { className: 'grid grid-cols-3 gap-3 mb-4' },
            createElement('div', { className: 'bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-4 text-center' },
              createElement('p', { className: 'text-3xl font-bold text-emerald-400' }, joinersSummary.total_joined_groups),
              createElement('p', { className: 'text-xs text-slate-400 mt-1' }, 'مجموعة منضم إليها')
            ),
            createElement('div', { className: 'bg-amber-500/10 border border-amber-500/30 rounded-lg p-4 text-center' },
              createElement('p', { className: 'text-3xl font-bold text-amber-400' }, joinersSummary.total_already_member),
              createElement('p', { className: 'text-xs text-slate-400 mt-1' }, 'منضم مسبقاً')
            ),
            createElement('div', { className: 'bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-center' },
              createElement('p', { className: 'text-3xl font-bold text-red-400' }, joinersSummary.total_banned),
              createElement('p', { className: 'text-xs text-slate-400 mt-1' }, 'مجموعات ممنوعة')
            )
          ) : null,
          createElement('h3', { className: 'text-sm font-semibold text-slate-300 mb-3' }, 'حسابات الفدائيين:'),
          createElement('div', { className: 'space-y-3' },
            ...joiners.map((j) => createElement(
              'div',
              { key: j.phone, className: 'bg-slate-800/50 rounded-lg p-4 border border-slate-700' },
              createElement('div', { className: 'flex items-center justify-between mb-2' },
                createElement('div', null,
                  createElement('p', { className: 'text-white font-mono text-sm' }, j.phone),
                  createElement('p', { className: 'text-xs text-slate-400' }, j.display_name || 'بدون اسم')
                ),
                createElement('span', { className: `text-xs px-3 py-1 rounded-full ${j.connected ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}` },
                  j.connected ? '✅ متصل' : '❌ غير متصل'
                )
              ),
              createElement('div', { className: 'grid grid-cols-3 gap-2 text-xs' },
                createElement('div', { className: 'bg-slate-900/50 rounded p-2 text-center' },
                  createElement('p', { className: 'text-slate-400' }, 'انضمامات اليوم'),
                  createElement('p', { className: 'text-blue-400 font-bold' }, `${j.daily_joins}/${j.daily_limit}`)
                ),
                createElement('div', { className: 'bg-slate-900/50 rounded p-2 text-center' },
                  createElement('p', { className: 'text-slate-400' }, 'الحالة'),
                  createElement('p', { className: j.joiner_enabled ? 'text-emerald-400 font-bold' : 'text-red-400 font-bold' },
                    j.joiner_enabled ? 'مفعّل' : 'معطّل'
                  )
                ),
                createElement('div', { className: 'bg-slate-900/50 rounded p-2 text-center' },
                  createElement('p', { className: 'text-slate-400' }, 'آخر انضمام'),
                  createElement('p', { className: 'text-slate-300 font-bold' },
                    j.last_join_timestamp
                      ? new Date(j.last_join_timestamp).toLocaleString('ar-SA', {hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short'})
                      : 'أبداً'
                  )
                )
              )
            ))
          )
        ) : createElement(
          'div',
          { className: 'flex flex-col flex-1 overflow-hidden' },
          // Search
          createElement('div', { className: 'p-4 border-b border-slate-700' },
            createElement('div', { className: 'relative' },
              createElement(Search, { className: 'absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400' }),
              createElement(Input, {
                placeholder: 'ابحث في الروابط...',
                value: searchQuery,
                onChange: (e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value),
                className: 'bg-slate-800/50 border-slate-700 text-white placeholder:text-slate-500 pr-10'
              })
            )
          ),
          // Links list
          createElement(ScrollArea, { className: 'flex-1 p-4' },
            filteredLinks.length === 0 ? createElement('div', { className: 'text-center py-20' },
              createElement(Link2, { className: 'w-12 h-12 mx-auto text-slate-600 mb-4' }),
              createElement('p', { className: 'text-slate-500' }, 'لا توجد روابط')
            ) : createElement('div', null,
              ...filteredLinks.map((link) => createElement(LinkCard, { key: link.id, link }))
            )
          )
        )
      )
    )
  )
}
