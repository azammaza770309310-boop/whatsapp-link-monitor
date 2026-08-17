'use client'

import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import {
  MessageCircle, Send, Search, Users, Link2,
  RefreshCw, ExternalLink, Phone, MapPin, Clock, Globe, ArrowRight
} from 'lucide-react'

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
  // AI fields (from Supabase — may be null if not yet analyzed)
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

interface CountryStat {
  country: string
  count: number
  percentage: number
}

// API base URL — البوت يخدم API endpoints على Render
// هذا هو المصدر الوحيد للبيانات — لا نستخدم Supabase مباشرة (RLS مفعّل)
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://whatsapp-userbot-yzm7.onrender.com'
// Supabase معطّل — RLS يمنع الوصول المباشر من الواجهة
const SUPABASE_URL = ''
const SUPABASE_KEY = ''

const COUNTRY_KEYWORDS: Record<string, string[]> = {
  'السعودية': ['السعودية', 'saudi', 'ksa', 'السعودي', 'الاهلية', 'الأهلية', 'دار الحكمة', 'اليمامة', 'ابن رشد', 'الملك', 'KAU', 'KSU', 'KFU', 'KFUPM', 'PSAU', 'UQU', 'JU', 'TAU', 'BAU', 'NU', 'IMAMU'],
  'الكويت': ['الكويت', 'kuwait', 'الكويتي', 'AUM', 'AUK', 'GUST', 'الكندي', 'PAAET', 'KU '],
  'قطر': ['قطر', 'qatar', 'القطري', 'Carnegie', 'Georgetown', 'HBKU', 'QU'],
  'البحرين': ['البحرين', 'bahrain', 'البحريني', 'Ahlia', 'AMA', 'المنامة'],
  'الإمارات': ['الإمارات', 'UAE', 'الإماراتي', 'Khalifa', 'Zayed', 'Sharjah', 'دبي', 'أبوظبي', 'الشارقة', 'UAEU', 'UOS']
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

/**
 * Defense-in-depth: only allow http(s) URLs in href attributes.
 * React 19 blocks javascript: URLs by default, but explicit validation
 * prevents any future regression and handles edge cases (data: URIs, etc).
 */
function safeUrl(url: string | null | undefined): string | null {
  if (!url || typeof url !== 'string') return null
  const trimmed = url.trim()
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return null
}

type ViewType = 'all' | 'whatsapp' | 'telegram' | 'ai_approved' | 'ai_rejected' | 'ai_ads' | string // string = country name

export default function Home() {
  const [links, setLinks] = useState<LinkItem[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [countryStats, setCountryStats] = useState<CountryStat[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [currentView, setCurrentView] = useState<ViewType>('all')
  const [allLinks, setAllLinks] = useState<LinkItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [usingMockData, setUsingMockData] = useState(false)
  // AI links fetched on-demand when user clicks an AI stat card
  const [aiFilterLinks, setAiFilterLinks] = useState<LinkItem[] | null>(null)
  const [aiFilterLoading, setAiFilterLoading] = useState(false)
  const [deployCheck, setDeployCheck] = useState<Record<string, unknown> | null>(null)

  // ===== Resilient fetch helper =====
  // يحاول الاتصال 3 مرات بفاصل 1.5 ثانية، مع timeout 10 ثوانٍ لكل محاولة.
  // يعيد null عند الفشل (لا يرمي استثناء) حتى لا يكسر الـ Promise.all.
  const fetchWithRetry = useCallback(async (url: string, maxRetries = 3): Promise<Response | null> => {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const controller = new AbortController()
        const timeout = setTimeout(() => controller.abort(), 10000)
        const response = await fetch(url, {
          headers: { 'Accept': 'application/json' },
          signal: controller.signal,
        })
        clearTimeout(timeout)
        if (response.ok || response.status === 400) return response
        // 404 / 500 → حاول مرة ثانية
        if (attempt < maxRetries) {
          await new Promise(r => setTimeout(r, 1500))
          continue
        }
        return response
      } catch (err) {
        // abort timeout أو network error
        if (attempt === maxRetries) {
          console.warn(`[fetch] ${url} failed after ${maxRetries} attempts:`, err)
          return null
        }
        await new Promise(r => setTimeout(r, 1500))
      }
    }
    return null
  }, [])

  const fetchLinks = useCallback(async () => {
    // استخدم API endpoint أولاً (المصدر الحقيقي)
    try {
      const response = await fetchWithRetry(`${API_URL}/api/links?limit=200`)
      if (response && response.ok) {
        const data = await response.json()
        const links = data.links || data || []
        if (Array.isArray(links) && links.length > 0) {
          setAllLinks(links)
          setLoading(false)
          setError(null)
          setUsingMockData(false)
          return
        }
        // لو القائمة فاضية لكن الـ API شغال
        if (Array.isArray(links) && links.length === 0) {
          setAllLinks([])
          setLoading(false)
          setError(null)
          setUsingMockData(false)
          return
        }
      } else if (response && !response.ok) {
        // API رجع خطأ
        setError(`API error: ${response.status}`)
      }
    } catch {
      // API غير متاح
    }
    // Fallback: لا يوجد Supabase مباشر (RLS معطل) — استخدم mock
    setAllLinks(mockLinks)
    setLinks(mockLinks)
    setLoading(false)
    setUsingMockData(true)
  }, [fetchWithRetry])

  const fetchStats = useCallback(async () => {
    // استخدم API endpoint أولاً
    try {
      const response = await fetchWithRetry(`${API_URL}/api/stats`)
      if (response && response.ok) {
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
        setError(null)
        setUsingMockData(false)
        return
      }
    } catch {
      // API غير متاح
    }
    // Fallback: لا يوجد Supabase مباشر
    if (!stats) {
      setStats(mockStats)
      setUsingMockData(true)
    }
  }, [fetchWithRetry, stats])

  // ===== Deploy check — تشخيص شامل للحالة =====
  const fetchDeployCheck = useCallback(async () => {
    try {
      const response = await fetchWithRetry(`${API_URL}/api/deploy_check`)
      if (response && response.ok) {
        const data = await response.json()
        setDeployCheck(data)
      }
    } catch {
      // silent — deploy check is optional
    }
  }, [fetchWithRetry])

  const fetchCountryStats = useCallback(async () => {
    // احسب إحصائيات الدول من allLinks المحلية (ما يحتاج Supabase مباشر)
    // هذا أسرع ويتجنب مشاكل RLS
    try {
      const counts: Record<string, number> = {}
      let total = 0
      // نستخدم allLinks لو موجودة، وإلا نجلب من API
      let dataToAnalyze: LinkItem[] = allLinks
      if (dataToAnalyze.length === 0) {
        const response = await fetchWithRetry(`${API_URL}/api/links?limit=500`)
        if (response && response.ok) {
          const data = await response.json()
          dataToAnalyze = data.links || []
        }
      }
      for (const item of dataToAnalyze) {
        const text = `${item.message_text || ''} ${item.group_name || ''} ${item.ai_country || ''}`
        const country = item.ai_country || detectCountry(text)
        if (country) {
          counts[country] = (counts[country] || 0) + 1
          total++
        }
      }
      const result: CountryStat[] = Object.entries(counts).map(([country, count]) => ({
        country,
        count,
        percentage: total > 0 ? Math.round((count / total) * 100) : 0
      })).sort((a, b) => b.count - a.count)
      setCountryStats(result)
    } catch {
      if (countryStats.length === 0) {
        setCountryStats(mockCountryStats)
        setUsingMockData(true)
      }
    }
  }, [allLinks, fetchWithRetry, countryStats.length])

  useEffect(() => {
    const load = async () => {
      await Promise.all([fetchLinks(), fetchStats(), fetchCountryStats(), fetchDeployCheck()])
    }
    load()
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [fetchLinks, fetchStats, fetchCountryStats, fetchDeployCheck])

  // فلترة الروابط حسب العرض الحالي
  useEffect(() => {
    // حالات AI: نستخدم قائمة منفصلة محمّلة عند الطلب من الـ API
    if (currentView === 'ai_approved' || currentView === 'ai_rejected' || currentView === 'ai_ads') {
      // القائمة محمّلة بالفعل في aiFilterLinks — فقط فلترة البحث محلياً
      let filtered = [...(aiFilterLinks || [])]
      if (searchQuery) {
        const q = searchQuery.toLowerCase()
        filtered = filtered.filter(l =>
          l.link?.toLowerCase().includes(q) ||
          l.message_text?.toLowerCase().includes(q) ||
          l.group_name?.toLowerCase().includes(q) ||
          l.sender_name?.toLowerCase().includes(q)
        )
      }
      setLinks(filtered)
      return
    }

    let filtered = [...allLinks]

    if (currentView === 'whatsapp') {
      filtered = filtered.filter(l => l.link_type === 'whatsapp')
    } else if (currentView === 'telegram') {
      filtered = filtered.filter(l => l.link_type === 'telegram')
    } else if (currentView !== 'all') {
      // فلترة حسب الدولة
      filtered = filtered.filter(l => {
        const text = `${l.message_text || ''} ${l.group_name || ''}`
        return detectCountry(text) === currentView
      })
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

    setLinks(filtered)
  }, [currentView, searchQuery, allLinks, aiFilterLinks])

  // تحميل روابط AI عند اختيار عرض AI
  const fetchAiLinks = useCallback(async (mode: 'ai_approved' | 'ai_rejected' | 'ai_ads') => {
    setAiFilterLoading(true)
    setAiFilterLinks(null)
    try {
      const params = new URLSearchParams({ limit: '200' })
      if (mode === 'ai_approved') params.set('ai_approved', 'true')
      else if (mode === 'ai_rejected') params.set('ai_approved', 'false')
      else if (mode === 'ai_ads') params.set('ai_is_ad', 'true')

      const response = await fetch(`${API_URL}/api/links?${params.toString()}`, {
        headers: { 'Accept': 'application/json' }
      })
      if (response.ok) {
        const data = await response.json()
        setAiFilterLinks(data.links || [])
      } else {
        setAiFilterLinks([])
      }
    } catch {
      setAiFilterLinks([])
    } finally {
      setAiFilterLoading(false)
    }
  }, [])

  // when user navigates to an AI view, fetch the matching links
  useEffect(() => {
    if (currentView === 'ai_approved') {
      fetchAiLinks('ai_approved')
    } else if (currentView === 'ai_rejected') {
      fetchAiLinks('ai_rejected')
    } else if (currentView === 'ai_ads') {
      fetchAiLinks('ai_ads')
    } else if (aiFilterLinks !== null) {
      // reset when leaving AI view — guarded by null check to avoid loop
      setAiFilterLinks(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentView])

  const countryColors: Record<string, string> = {
    'السعودية': 'from-emerald-500 to-green-400',
    'الكويت': 'from-blue-500 to-cyan-400',
    'قطر': 'from-purple-500 to-pink-400',
    'البحرين': 'from-amber-500 to-orange-400',
    'الإمارات': 'from-rose-500 to-red-400'
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white">
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-emerald-500/10 rounded-full blur-3xl" />
        <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-blue-500/10 rounded-full blur-3xl" />
      </div>

      <div className="relative z-10 container mx-auto px-4 py-8 max-w-6xl">
        {/* الرأس */}
        <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
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
            <Button variant="ghost" size="sm" onClick={() => { fetchLinks(); fetchStats(); fetchCountryStats(); }}
              className="text-slate-400 hover:text-white">
              <RefreshCw className="w-4 h-4" />
            </Button>
          </div>
        </motion.div>

        {/* عرض الكل - الإحصائيات والتبويبات */}
        {currentView === 'all' && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            {/* Deploy Check Banner — يعرض حالة النشر والمشاكل */}
            {deployCheck && (
              <DeployCheckBanner report={deployCheck} />
            )}
            {/* Error banner — surface real errors instead of silently using mock data */}
            {error && (
              <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
                ⚠️ فشل تحميل البيانات: {error}
                {usingMockData && <span className="block text-xs text-red-400/70 mt-1">يعرض الآن بيانات تجريبية</span>}
              </div>
            )}
            {/* بطاقات الإحصائيات كأزرار */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4 mb-6">
              <StatCard icon={<Link2 className="w-5 h-5" />} label="إجمالي الروابط" value={stats?.total_links ?? 0}
                gradient="from-emerald-500/20 to-emerald-500/5" iconColor="text-emerald-400"
                onClick={() => setCurrentView('all')} active={true} />
              <StatCard icon={<MessageCircle className="w-5 h-5" />} label="🟢 واتساب" value={stats?.whatsapp_links ?? 0}
                gradient="from-green-500/20 to-green-500/5" iconColor="text-green-400"
                onClick={() => setCurrentView('whatsapp')} active={false} />
              <StatCard icon={<Send className="w-5 h-5" />} label="🔵 تيليجرام" value={stats?.telegram_links ?? 0}
                gradient="from-blue-500/20 to-blue-500/5" iconColor="text-blue-400"
                onClick={() => setCurrentView('telegram')} active={false} />
              <StatCard icon={<Users className="w-5 h-5" />} label="المراقبون" value={stats?.active_watchers ?? 0}
                gradient="from-purple-500/20 to-purple-500/5" iconColor="text-purple-400"
                onClick={() => {}} active={false} />
            </div>

            {/* التحليل الإحصائي - أزرار قابلة للضغط */}
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
                    {countryStats.map((cs, i) => (
                      <button key={cs.country} onClick={() => setCurrentView(cs.country)}
                        className="text-right hover:scale-105 transition-transform">
                        <Card className={`bg-gradient-to-br ${countryColors[cs.country] || 'from-slate-600 to-slate-700'} border-0 overflow-hidden`}>
                          <CardContent className="p-4">
                            <div className="flex items-center justify-between mb-2">
                              <span className="text-sm font-bold text-white">{cs.country}</span>
                              <Badge className="bg-black/30 text-white border-0">{cs.count}</Badge>
                            </div>
                            <div className="w-full bg-black/30 rounded-full h-2 overflow-hidden">
                              <div className="h-full bg-white/80 rounded-full" style={{ width: `${cs.percentage}%` }} />
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

            {/* ===== AI Stats Card ===== */}
            <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
              <CardHeader>
                <CardTitle className="flex items-center justify-between text-lg">
                  <span className="flex items-center gap-2">
                    <span className="text-2xl">🤖</span>
                    تحليل الذكاء الاصطناعي
                  </span>
                  {stats?.ai_stats && (
                    <Badge variant="outline" className={
                      stats.ai_stats.ai_batch_mode
                        ? 'border-amber-500/40 text-amber-400 bg-amber-500/10'
                        : 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
                    }>
                      {stats.ai_stats.ai_batch_mode ? '⏭️ Batch Mode' : '🤖 AI Active'}
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {/* AI Approved */}
                  <button onClick={() => setCurrentView('ai_approved')} className="text-right hover:scale-105 transition-transform">
                    <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
                      <p className="text-2xl font-bold text-emerald-400">{stats?.ai_stats?.ai_approved ?? 0}</p>
                      <p className="text-xs text-slate-400 mt-1">✅ موافق عليه</p>
                    </div>
                  </button>
                  {/* AI Rejected */}
                  <button onClick={() => setCurrentView('ai_rejected')} className="text-right hover:scale-105 transition-transform">
                    <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-center">
                      <p className="text-2xl font-bold text-red-400">{stats?.ai_stats?.ai_rejected ?? 0}</p>
                      <p className="text-xs text-slate-400 mt-1">❌ مرفوض</p>
                    </div>
                  </button>
                  {/* AI Ads */}
                  <button onClick={() => setCurrentView('ai_ads')} className="text-right hover:scale-105 transition-transform">
                    <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center">
                      <p className="text-2xl font-bold text-amber-400">{stats?.ai_stats?.ai_ads ?? 0}</p>
                      <p className="text-xs text-slate-400 mt-1">⚠️ إعلان</p>
                    </div>
                  </button>
                  {/* AI Pending */}
                  <div className="bg-slate-700/30 border border-slate-700 rounded-lg p-3 text-center">
                    <p className="text-2xl font-bold text-slate-300">{stats?.ai_stats?.ai_pending ?? 0}</p>
                    <p className="text-xs text-slate-400 mt-1">⏳ لم يُفحص</p>
                  </div>
                </div>
                {stats?.ai_stats?.ai_batch_mode && (
                  <p className="text-xs text-slate-500 mt-3 leading-relaxed">
                    💡 وضع المعالجة المجمّعة (Batch Mode) مُفعّل — يتم نشر الروابط بدون فحص AI لتسريع معالجة القائمة المتراكمة.
                    أرسل <code className="bg-slate-700/50 px-1.5 py-0.5 rounded text-blue-300">/ai_mode on</code> للبوت لإعادة تفعيل الفحص.
                  </p>
                )}
              </CardContent>
            </Card>

            {/* البحث */}
            <div className="relative mb-4">
              <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <Input placeholder="ابحث في الروابط..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                className="bg-slate-800/50 border-slate-700 text-white placeholder:text-slate-500 pr-10" />
            </div>

            {/* ===== Joiner Dashboard ===== */}
            <JoinerDashboard />
          </motion.div>
        )}

        {/* عرض مخصص (واتساب/تيليجرام/دولة/AI) */}
        {currentView !== 'all' && (
          <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} className="mb-6">
            <div className="flex items-center gap-3 mb-4">
              <Button variant="ghost" size="sm" onClick={() => setCurrentView('all')}
                className="text-slate-400 hover:text-white">
                <ArrowRight className="w-4 h-4 ml-2" />
                رجوع
              </Button>
              <h2 className="text-xl font-bold">
                {currentView === 'whatsapp' && '🟢 روابط واتساب'}
                {currentView === 'telegram' && '🔵 روابط تيليجرام'}
                {currentView === 'ai_approved' && '✅ روابط موافق عليها (AI)'}
                {currentView === 'ai_rejected' && '❌ روابط مرفوضة (AI)'}
                {currentView === 'ai_ads' && '⚠️ روابط مُصنّفة كإعلانات (AI)'}
                {currentView !== 'whatsapp' && currentView !== 'telegram' &&
                 currentView !== 'ai_approved' && currentView !== 'ai_rejected' && currentView !== 'ai_ads' && `🌐 روابط ${currentView}`}
              </h2>
              <Badge className="bg-slate-700 text-white border-0">
                {aiFilterLoading ? '...' : links.length}
              </Badge>
            </div>
            <div className="relative mb-4">
              <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <Input placeholder="ابحث..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                className="bg-slate-800/50 border-slate-700 text-white placeholder:text-slate-500 pr-10" />
            </div>
          </motion.div>
        )}

        {/* قائمة الروابط */}
        <div className="mb-4 flex items-center justify-between">
          <p className="text-sm text-slate-400">
            {aiFilterLoading ? '⏳ جارٍ تحميل روابط AI...' : `عرض ${links.length} رابط`}
          </p>
        </div>

        <ScrollArea className="h-[500px] pr-4">
          {loading || aiFilterLoading ? (
            <div className="space-y-4">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-32 w-full bg-slate-800/50 rounded-xl" />)}</div>
          ) : links.length === 0 ? (
            <div className="text-center py-20">
              <Link2 className="w-12 h-12 mx-auto text-slate-600 mb-4" />
              <p className="text-slate-500">لا توجد روابط</p>
            </div>
          ) : (
            <AnimatePresence mode="popLayout">
              {links.map((link, index) => (
                <motion.div key={link.id || index} initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.95 }} transition={{ delay: index * 0.03 }} layout>
                  <LinkCard link={link} />
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </ScrollArea>

        <footer className="mt-10 text-center text-slate-500 text-xs"><p>نظام مراقبة الروابط © 2026</p></footer>
      </div>
    </div>
  )
}

function StatCard({ icon, label, value, gradient, iconColor, onClick, active }: {
  icon: React.ReactNode; label: string; value: number; gradient: string; iconColor: string;
  onClick: () => void; active: boolean
}) {
  return (
    <button onClick={onClick} className="text-right hover:scale-105 transition-transform">
      <Card className={`bg-gradient-to-br ${gradient} border-slate-700/50 backdrop-blur-sm overflow-hidden ${active ? 'ring-2 ring-emerald-500' : ''}`}>
        <CardContent className="p-4">
          <div className="flex items-center justify-between">
            <div><p className="text-slate-400 text-xs mb-1">{label}</p><p className="text-2xl font-bold text-white">{value}</p></div>
            <div className={`${iconColor} opacity-80`}>{icon}</div>
          </div>
        </CardContent>
      </Card>
    </button>
  )
}

function LinkCard({ link }: { link: LinkItem }) {
  const isWhatsapp = link.link_type === 'whatsapp'
  const date = new Date(link.created_at)
  const timeStr = date.toLocaleString('ar-SA', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  const country = link.ai_country || detectCountry(`${link.message_text || ''} ${link.group_name || ''}`)
  const href = safeUrl(link.link)

  // AI badge rendering
  // ai_approved === true  → ✅ موافق عليه
  // ai_approved === false → ❌ مرفوض
  // ai_approved === null  → ⏳ لم يُفحص
  const aiApproved = link.ai_approved
  const aiIsAd = link.ai_is_ad === true
  const aiDescription = link.ai_description

  return (
    <Card className="mb-3 bg-slate-800/30 border-slate-700/50 backdrop-blur-sm hover:bg-slate-800/50 transition-all duration-300 overflow-hidden">
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2 flex-wrap">
            <div className={`w-3 h-3 rounded-full ${isWhatsapp ? 'bg-green-500' : 'bg-blue-500'} shadow-lg`} />
            <Badge variant="outline" className={isWhatsapp ? 'border-green-500/30 text-green-400' : 'border-blue-500/30 text-blue-400'}>
              {isWhatsapp ? '🟢 واتساب' : '🔵 تيليجرام'}
            </Badge>
            {country && <Badge variant="outline" className="border-purple-500/30 text-purple-400">{country}</Badge>}
            {/* AI verification badges */}
            {aiApproved === true && (
              <Badge variant="outline" className="border-emerald-500/40 text-emerald-400 bg-emerald-500/10">
                ✅ AI موافق
              </Badge>
            )}
            {aiApproved === false && (
              <Badge variant="outline" className="border-red-500/40 text-red-400 bg-red-500/10">
                ❌ AI مرفوض
              </Badge>
            )}
            {aiApproved === null && (
              <Badge variant="outline" className="border-slate-600/40 text-slate-500">
                ⏳ لم يُفحص
              </Badge>
            )}
            {aiIsAd && (
              <Badge variant="outline" className="border-amber-500/40 text-amber-400 bg-amber-500/10">
                ⚠️ إعلان
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1 text-slate-500 text-xs"><Clock className="w-3 h-3" />{timeStr}</div>
        </div>
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer" className="block mb-3 group">
            <div className="flex items-center gap-2 bg-slate-900/50 rounded-lg p-3 hover:bg-slate-900/80 transition-colors">
              <ExternalLink className="w-4 h-4 text-slate-400 group-hover:text-white shrink-0" />
              <span className="text-sm text-blue-300 group-hover:text-blue-200 truncate font-mono">{href}</span>
            </div>
          </a>
        ) : (
          <div className="mb-3 bg-slate-900/50 rounded-lg p-3">
            <div className="flex items-center gap-2">
              <ExternalLink className="w-4 h-4 text-slate-500 shrink-0" />
              <span className="text-sm text-slate-400 truncate font-mono">{link.link || '(no URL)'}</span>
            </div>
          </div>
        )}
        {/* AI description — show prominently if available */}
        {aiDescription && (
          <div className="mb-2 bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-2 text-xs text-emerald-300/90">
            <span className="font-semibold">🤖 وصف AI:</span> {aiDescription}
          </div>
        )}
        <div className="grid grid-cols-2 gap-2 text-xs">
          {link.group_name && <div className="flex items-center gap-1.5 text-slate-400"><MapPin className="w-3 h-3 shrink-0" /><span className="truncate">{link.group_name}</span></div>}
          {link.sender_name && <div className="flex items-center gap-1.5 text-slate-400"><Users className="w-3 h-3 shrink-0" /><span className="truncate">{link.sender_name}</span></div>}
          {link.sender_contact && <div className="flex items-center gap-1.5 text-slate-400"><Phone className="w-3 h-3 shrink-0" /><span className="truncate">{link.sender_contact}</span></div>}
        </div>
        {link.message_text && <div className="mt-3 bg-slate-900/40 rounded-lg p-3 text-xs text-slate-400 max-h-24 overflow-hidden">{link.message_text}</div>}
      </CardContent>
    </Card>
  )
}

const mockLinks: LinkItem[] = [
  { id: 1, link: 'https://chat.whatsapp.com/ABC123xyz', link_type: 'whatsapp', message_text: 'انضموا لمجموعة جامعة الأهلية السعودية', group_name: 'دعم SEU للطلاب الجدد', sender_name: 'محمد', sender_contact: '📱 +966500000000', source_phone: '+967770309310', message_link: null, created_at: new Date(Date.now() - 1000 * 60 * 5).toISOString() },
  { id: 2, link: 'https://t.me/kuwait_university', link_type: 'telegram', message_text: 'قناة جامعة الكويت الرسمية', group_name: 'مجموعة الكويت الجامعية', sender_name: 'أحمد', sender_contact: '✈️ @ahmed', source_phone: '+96550000000', message_link: null, created_at: new Date(Date.now() - 1000 * 60 * 30).toISOString() },
  { id: 3, link: 'https://chat.whatsapp.com/DEF456uvw', link_type: 'whatsapp', message_text: 'مجموعة جامعة قطر للطلاب', group_name: 'طلاب قطر', sender_name: 'سارة', sender_contact: '📱 +97430000000', source_phone: '+967770309310', message_link: null, created_at: new Date(Date.now() - 1000 * 60 * 60).toISOString() }
]

// ===== Joiner Dashboard Types =====
interface JoinedGroup {
  id: number
  group_title: string | null
  group_link: string
  status: string
  joined_by_phone: string | null
  join_date: string | null
  member_count: number | null
}

interface JoinerStats {
  total_joined: number
  pending_groups: number
  active_joiners: number
}

function JoinerDashboard() {
  const [joinedGroups, setJoinedGroups] = useState<JoinedGroup[]>([])
  const [joinerStats, setJoinerStats] = useState<JoinerStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [joinersData, setJoinersData] = useState<Array<{
    phone: string; display_name: string; connected: boolean;
    daily_joins: number; daily_limit: number; last_join_timestamp: string
  }>>([])
  const [joinersSummary, setJoinersSummary] = useState<{
    total_joiners: number; connected_joiners: number;
    total_joined_groups: number; total_already_member: number; total_banned: number
  } | null>(null)

  const fetchJoinerData = useCallback(async () => {
    // استخدم API endpoint أولاً (المصدر الحقيقي من SQLite group_states)
    try {
      const response = await fetch(`${API_URL}/api/joined_groups`, {
        headers: { 'Accept': 'application/json' }
      })
      if (response.ok) {
        const data = await response.json()
        const groups = data.joined_groups || []
        const stats = data.stats || {}
        setJoinedGroups(groups)
        setJoinerStats({
          total_joined: stats.total_joined || 0,
          pending_groups: stats.pending_groups || 0,
          active_joiners: stats.active_joiners || 0
        })
        setLoading(false)
      }
    } catch {
      // API غير متاح
    }

    // اجلب حالة الفدائيين من endpoint الجديد
    try {
      const response = await fetch(`${API_URL}/api/joiners_status`, {
        headers: { 'Accept': 'application/json' }
      })
      if (response.ok) {
        const data = await response.json()
        setJoinersData(data.joiners || [])
        setJoinersSummary(data.summary || null)
        // حدّث الإحصائيات لو ما في data من /api/joined_groups
        if (!joinerStats && data.summary) {
          setJoinerStats({
            total_joined: data.summary.total_joined_groups || 0,
            pending_groups: 0,
            active_joiners: data.summary.connected_joiners || 0
          })
        }
      }
    } catch {
      // silent
    }

    // Fallback: لا يوجد Supabase مباشر
    if (!SUPABASE_URL || !SUPABASE_KEY) {
      if (!joinerStats) {
        setJoinerStats({ total_joined: 0, pending_groups: 0, active_joiners: 0 })
      }
      setLoading(false)
      return
    }
  }, [])

  useEffect(() => { fetchJoinerData() }, [fetchJoinerData])

  if (loading) return <div className="mb-6"><Skeleton className="h-40 w-full bg-slate-800/50 rounded-xl" /></div>

  // حتى لو ما فيه بيانات، اعرض اللوحة مع رسالة فارغة
  const stats = joinerStats || { total_joined: 0, pending_groups: 0, active_joiners: 0 }
  const isEmpty = stats.total_joined === 0 && stats.pending_groups === 0 && stats.active_joiners === 0

  return (
    <Card className="bg-slate-800/30 border-slate-700/50 backdrop-blur-sm mb-6">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg">
          <span className="text-2xl">🚀</span>
          لوحة الفدائي (Joiner Dashboard)
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Stats Cards — دائماً تظهر */}
        <div className="grid grid-cols-3 gap-3 mb-4">
          <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-center">
            <p className="text-2xl font-bold text-emerald-400">{stats.total_joined}</p>
            <p className="text-xs text-slate-400 mt-1">مجموعة منضم إليها</p>
          </div>
          <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-center">
            <p className="text-2xl font-bold text-amber-400">{stats.pending_groups}</p>
            <p className="text-xs text-slate-400 mt-1">مجموعة معلقة</p>
          </div>
          <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-3 text-center">
            <p className="text-2xl font-bold text-purple-400">{stats.active_joiners}</p>
            <p className="text-xs text-slate-400 mt-1">حسابات فدائية نشطة</p>
          </div>
        </div>

        {/* Joiners List — عرض كل الفدائيين وحالتهم */}
        {joinersData.length > 0 && (
          <div className="mb-4">
            <h4 className="text-sm font-semibold text-slate-300 mb-2">🚀 حسابات الفدائيين:</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {joinersData.map((j) => (
                <div key={j.phone} className="bg-slate-700/30 rounded-lg p-3 border border-slate-700">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-white font-mono text-xs">{j.phone}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      j.connected ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'
                    }`}>
                      {j.connected ? '✅ متصل' : '❌ غير متصل'}
                    </span>
                  </div>
                  <div className="text-xs text-slate-400 flex justify-between">
                    <span>انضمامات اليوم: <span className="text-blue-400">{j.daily_joins}/{j.daily_limit}</span></span>
                    {j.last_join_timestamp && (
                      <span>آخر: {new Date(j.last_join_timestamp).toLocaleString('ar-SA', {hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short'})}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {joinersSummary && (
              <div className="mt-2 text-xs text-slate-500 flex gap-4 flex-wrap">
                <span>إجمالي الفدائيين: <span className="text-purple-400">{joinersSummary.total_joiners}</span></span>
                <span>المتصلين: <span className="text-emerald-400">{joinersSummary.connected_joiners}</span></span>
                <span>مجموعات ممنوعة: <span className="text-red-400">{joinersSummary.total_banned}</span></span>
              </div>
            )}
          </div>
        )}

        {/* Joined Groups Table */}
        {joinedGroups.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 text-slate-400 text-xs">
                  <th className="text-right py-2 px-2">اسم المجموعة</th>
                  <th className="text-right py-2 px-2">الرابط</th>
                  <th className="text-right py-2 px-2">الأعضاء</th>
                  <th className="text-right py-2 px-2">الحساب</th>
                  <th className="text-right py-2 px-2">تاريخ الانضمام</th>
                  <th className="text-right py-2 px-2">الحالة</th>
                </tr>
              </thead>
              <tbody>
                {joinedGroups.map((g) => (
                  <tr key={g.id} className="border-b border-slate-800 hover:bg-slate-800/30">
                    <td className="py-2 px-2 text-white truncate max-w-[150px]">{g.group_title || 'غير معروف'}</td>
                    <td className="py-2 px-2">
                      <a href={g.group_link} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:text-blue-300 truncate inline-block max-w-[120px] font-mono text-xs">
                        {g.group_link}
                      </a>
                    </td>
                    <td className="py-2 px-2 text-slate-400">{g.member_count != null ? g.member_count.toLocaleString() : '-'}</td>
                    <td className="py-2 px-2 text-slate-400 font-mono text-xs">{g.joined_by_phone || '-'}</td>
                    <td className="py-2 px-2 text-slate-400 text-xs">{g.join_date ? new Date(g.join_date).toLocaleDateString('ar-SA') : '-'}</td>
                    <td className="py-2 px-2">
                      <Badge variant="outline" className={g.status === 'JOINED' ? 'border-emerald-500/30 text-emerald-400' : 'border-blue-500/30 text-blue-400'}>
                        {g.status === 'JOINED' ? '✅ ناجح' : '👤 منضم مسبقاً'}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Empty State — يظهر لما ما فيه مجموعات */}
        {isEmpty && (
          <div className="text-center py-8 px-4">
            <div className="text-4xl mb-3">🚀</div>
            <p className="text-slate-300 text-sm font-medium mb-2">لا توجد بيانات فدائي بعد</p>
            <p className="text-slate-500 text-xs leading-relaxed">
              أضف حساب فدائي عبر إرسال <code className="bg-slate-700/50 px-1.5 py-0.5 rounded text-blue-300">/login</code> للبوت
              واختيار "🚀 حساب فدائي"<br/>
              سيبدأ البوت بالانضمام التلقائي للمجموعات المكتشفة
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

const mockStats: Stats = { total_links: 156, whatsapp_links: 98, telegram_links: 58, active_watchers: 7 }
const mockCountryStats: CountryStat[] = [
  { country: 'السعودية', count: 65, percentage: 42 },
  { country: 'الكويت', count: 35, percentage: 22 },
  { country: 'الإمارات', count: 28, percentage: 18 },
  { country: 'قطر', count: 18, percentage: 11 },
  { country: 'البحرين', count: 10, percentage: 7 }
]

// ===== Deploy Check Banner — يعرض تقرير صحة النشر =====
function DeployCheckBanner({ report }: { report: Record<string, unknown> }) {
  const verdict = (report.verdict as string) || 'UNKNOWN'
  const issues = (report.issues as string[]) || []
  const supabase = (report.supabase as Record<string, unknown>) || {}
  const telegram = (report.telegram as Record<string, unknown>) || {}
  const ai = (report.ai as Record<string, unknown>) || {}
  const envVars = (report.env_vars as Record<string, string>) || {}
  const queue = (report.queue as Record<string, number>) || {}

  const isHealthy = verdict === 'HEALTHY'
  const supabaseKeyType = supabase.key_type as string | undefined
  const supabaseReachable = supabase.reachable as boolean | undefined
  const botConnected = telegram.bot_connected as boolean | undefined
  const monitorsCount = telegram.monitors_count as number | undefined
  const joinersCount = telegram.joiners_count as number | undefined

  return (
    <Card className={`mb-4 backdrop-blur-sm border ${
      isHealthy
        ? 'bg-emerald-500/5 border-emerald-500/30'
        : 'bg-amber-500/5 border-amber-500/30'
    }`}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center justify-between text-sm">
          <span className="flex items-center gap-2">
            <span className="text-lg">{isHealthy ? '✅' : '⚠️'}</span>
            تقرير النشر ({verdict})
          </span>
          <span className="text-xs text-slate-400">
            {issues.length > 0 ? `${issues.length} مشاكل` : 'كل شيء سليم'}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-2">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          {/* Supabase status */}
          <div className={`p-2 rounded ${supabaseReachable ? 'bg-emerald-500/10' : 'bg-red-500/10'}`}>
            <p className="text-slate-400">Supabase</p>
            <p className={supabaseReachable ? 'text-emerald-400' : 'text-red-400'}>
              {supabaseReachable ? '✅ متصل' : '❌ غير متصل'}
            </p>
            {supabaseKeyType && (
              <p className={`text-[10px] mt-0.5 ${supabaseKeyType === 'service_role' ? 'text-emerald-400' : 'text-red-400'}`}>
                {supabaseKeyType === 'service_role' ? '🔑 service_role' : '🚨 anon (خطأ!)'}
              </p>
            )}
          </div>
          {/* Telegram bot */}
          <div className={`p-2 rounded ${botConnected ? 'bg-emerald-500/10' : 'bg-red-500/10'}`}>
            <p className="text-slate-400">Telegram Bot</p>
            <p className={botConnected ? 'text-emerald-400' : 'text-red-400'}>
              {botConnected ? '✅ متصل' : '❌ غير متصل'}
            </p>
          </div>
          {/* Monitors + Joiners */}
          <div className="p-2 rounded bg-slate-700/30">
            <p className="text-slate-400">الحسابات</p>
            <p className="text-slate-200">
              👁️ {monitorsCount ?? 0} مراقب
            </p>
            <p className="text-slate-200">
              🚀 {joinersCount ?? 0} فدائي
            </p>
          </div>
          {/* Queue */}
          <div className="p-2 rounded bg-slate-700/30">
            <p className="text-slate-400">قاعدة البيانات</p>
            <p className="text-slate-200">{queue.total_links ?? 0} رابط</p>
            <p className="text-amber-400">{queue.pending_links ?? 0} معلق</p>
          </div>
        </div>

        {/* AI status */}
        {ai.analyzer_enabled !== undefined && (
          <div className="mt-2 p-2 rounded bg-slate-700/30 text-xs">
            <span className="text-slate-400">🤖 AI: </span>
            <span className={ai.analyzer_enabled ? 'text-emerald-400' : 'text-slate-500'}>
              {ai.analyzer_enabled ? `مفعّل (${ai.providers_count} مفاتيح)` : 'غير مفعّل'}
            </span>
            <span className="text-slate-400 mr-3"> | الوضع: </span>
            <span className={ai.batch_mode ? 'text-amber-400' : 'text-emerald-400'}>
              {ai.batch_mode ? '⏭️ Batch (متخطّي)' : '🔍 فحص كامل'}
            </span>
          </div>
        )}

        {/* Issues list */}
        {issues.length > 0 && (
          <div className="mt-2 p-2 rounded bg-red-500/5 border border-red-500/20">
            <p className="text-red-300 text-xs font-semibold mb-1">⚠️ المشاكل المكتشفة:</p>
            <ul className="text-xs text-red-200/80 space-y-0.5">
              {issues.slice(0, 5).map((issue, i) => (
                <li key={i} className="leading-relaxed">• {issue}</li>
              ))}
              {issues.length > 5 && (
                <li className="text-red-400/60">+ {issues.length - 5} مشاكل أخرى...</li>
              )}
            </ul>
          </div>
        )}

        {/* Missing env vars (compact) */}
        {Object.values(envVars).some(v => v.includes('MISSING')) && (
          <div className="mt-2 text-xs text-amber-300">
            <span className="font-semibold">متغيرات بيئية مفقودة: </span>
            {Object.entries(envVars)
              .filter(([, v]) => v.includes('MISSING'))
              .map(([k]) => k)
              .join(', ')}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
