'use client'

import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { 
  MessageCircle, 
  Send, 
  Search, 
  TrendingUp, 
  Users, 
  Link2,
  RefreshCw,
  ExternalLink,
  Phone,
  MapPin,
  Clock
} from 'lucide-react'

// أنواع البيانات
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
}

interface Stats {
  total_links: number
  whatsapp_links: number
  telegram_links: number
  active_watchers: number
}

export default function Home() {
  const [links, setLinks] = useState<LinkItem[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [activeTab, setActiveTab] = useState('all')

  // جلب البيانات
  const fetchLinks = async () => {
    try {
      // في الإنتاج، استبدل هذا بـ API الـ Backend
      // هنا نستخدم Supabase مباشرة للعرض
      const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
      const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_KEY
      
      if (!supabaseUrl || !supabaseKey) {
        // بيانات تجريبية للعرض
        setLinks(mockLinks)
        setStats(mockStats)
        setLoading(false)
        return
      }

      const response = await fetch(`${supabaseUrl}/rest/v1/links?order=created_at.desc&limit=50`, {
        headers: {
          'apikey': supabaseKey,
          'Authorization': `Bearer ${supabaseKey}`
        }
      })
      const data = await response.json()
      setLinks(data || [])
      setLoading(false)
    } catch (error) {
      console.error('Fetch error:', error)
      setLinks(mockLinks)
      setStats(mockStats)
      setLoading(false)
    }
  }

  const fetchStats = async () => {
    try {
      const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
      const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_KEY
      
      if (!supabaseUrl || !supabaseKey) {
        setStats(mockStats)
        return
      }

      const headers = {
        'apikey': supabaseKey,
        'Authorization': `Bearer ${supabaseKey}`,
        'Prefer': 'count=exact'
      }

      const [totalRes, waRes, tgRes, watchRes] = await Promise.all([
        fetch(`${supabaseUrl}/rest/v1/links?select=id&limit=1`, { headers }),
        fetch(`${supabaseUrl}/rest/v1/links?link_type=eq.whatsapp&select=id&limit=1`, { headers }),
        fetch(`${supabaseUrl}/rest/v1/links?link_type=eq.telegram&select=id&limit=1`, { headers }),
        fetch(`${supabaseUrl}/rest/v1/watchers?is_active=eq.true&select=id&limit=1`, { headers })
      ])

      const getCount = (res: Response) => {
        const range = res.headers.get('content-range') || '0/0'
        return parseInt(range.split('/')[1] || '0')
      }

      setStats({
        total_links: getCount(totalRes),
        whatsapp_links: getCount(waRes),
        telegram_links: getCount(tgRes),
        active_watchers: getCount(watchRes)
      })
    } catch (error) {
      setStats(mockStats)
    }
  }

  useEffect(() => {
    let mounted = true
    const load = async () => {
      await fetchLinks()
      await fetchStats()
    }
    load()
    // تحديث كل 30 ثانية
    const interval = setInterval(load, 30000)
    return () => { mounted = false; clearInterval(interval) }
  }, [])

  // فلترة الروابط
  const filteredLinks = links.filter(link => {
    if (activeTab === 'whatsapp' && link.link_type !== 'whatsapp') return false
    if (activeTab === 'telegram' && link.link_type !== 'telegram') return false
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      return (
        link.link?.toLowerCase().includes(q) ||
        link.message_text?.toLowerCase().includes(q) ||
        link.group_name?.toLowerCase().includes(q) ||
        link.sender_name?.toLowerCase().includes(q)
      )
    }
    return true
  })

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white">
      {/* الخلفية المتوهجة */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-emerald-500/10 rounded-full blur-3xl" />
        <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-blue-500/10 rounded-full blur-3xl" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-purple-500/5 rounded-full blur-3xl" />
      </div>

      {/* المحتوى */}
      <div className="relative z-10 container mx-auto px-4 py-8 max-w-6xl">
        
        {/* الرأس */}
        <motion.div 
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-center mb-10"
        >
          <div className="flex items-center justify-center gap-3 mb-3">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-emerald-500 to-blue-500 flex items-center justify-center shadow-lg shadow-emerald-500/20">
              <Link2 className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-3xl md:text-4xl font-bold bg-gradient-to-r from-emerald-400 via-white to-blue-400 bg-clip-text text-transparent">
              مراقب الروابط
            </h1>
          </div>
          <p className="text-slate-400 text-sm md:text-base">
            نظام سحب روابط واتساب وتيليجرام من المجموعات الجامعية
          </p>
        </motion.div>

        {/* بطاقات الإحصائيات */}
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4 mb-8"
        >
          <StatCard
            icon={<Link2 className="w-5 h-5" />}
            label="إجمالي الروابط"
            value={stats?.total_links ?? 0}
            gradient="from-emerald-500/20 to-emerald-500/5"
            iconColor="text-emerald-400"
          />
          <StatCard
            icon={<MessageCircle className="w-5 h-5" />}
            label="روابط واتساب"
            value={stats?.whatsapp_links ?? 0}
            gradient="from-green-500/20 to-green-500/5"
            iconColor="text-green-400"
          />
          <StatCard
            icon={<Send className="w-5 h-5" />}
            label="روابط تيليجرام"
            value={stats?.telegram_links ?? 0}
            gradient="from-blue-500/20 to-blue-500/5"
            iconColor="text-blue-400"
          />
          <StatCard
            icon={<Users className="w-5 h-5" />}
            label="المراقبون"
            value={stats?.active_watchers ?? 0}
            gradient="from-purple-500/20 to-purple-500/5"
            iconColor="text-purple-400"
          />
        </motion.div>

        {/* أداة البحث والتبويبات */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="mb-6"
        >
          <div className="relative mb-4">
            <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <Input
              placeholder="ابحث في الروابط..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-slate-800/50 border-slate-700 text-white placeholder:text-slate-500 pr-10"
            />
          </div>

          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="bg-slate-800/50 border border-slate-700">
              <TabsTrigger value="all" className="data-[state=active]:bg-slate-700 text-slate-300">
                الكل
              </TabsTrigger>
              <TabsTrigger value="whatsapp" className="data-[state=active]:bg-green-600/20 data-[state=active]:text-green-400 text-slate-300">
                🟢 واتساب
              </TabsTrigger>
              <TabsTrigger value="telegram" className="data-[state=active]:bg-blue-600/20 data-[state=active]:text-blue-400 text-slate-300">
                🔵 تيليجرام
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </motion.div>

        {/* قائمة الروابط */}
        <div className="mb-4 flex items-center justify-between">
          <p className="text-sm text-slate-400">
            عرض {filteredLinks.length} رابط
          </p>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { fetchLinks(); fetchStats(); }}
            className="text-slate-400 hover:text-white"
          >
            <RefreshCw className="w-4 h-4 ml-2" />
            تحديث
          </Button>
        </div>

        <ScrollArea className="h-[600px] pr-4">
          {loading ? (
            <div className="space-y-4">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-32 w-full bg-slate-800/50 rounded-xl" />
              ))}
            </div>
          ) : filteredLinks.length === 0 ? (
            <div className="text-center py-20">
              <Link2 className="w-12 h-12 mx-auto text-slate-600 mb-4" />
              <p className="text-slate-500">لا توجد روابط بعد</p>
              <p className="text-slate-600 text-sm mt-2">سيتم عرض الروابط هنا فور سحبها</p>
            </div>
          ) : (
            <AnimatePresence mode="popLayout">
              {filteredLinks.map((link, index) => (
                <motion.div
                  key={link.id || index}
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                  transition={{ delay: index * 0.05 }}
                  layout
                >
                  <LinkCard link={link} />
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </ScrollArea>

        {/* التذييل */}
        <footer className="mt-10 text-center text-slate-500 text-xs">
          <p>نظام مراقبة الروابط © 2026</p>
        </footer>
      </div>
    </div>
  )
}

// بطاقة الإحصائيات
function StatCard({ icon, label, value, gradient, iconColor }: {
  icon: React.ReactNode
  label: string
  value: number
  gradient: string
  iconColor: string
}) {
  return (
    <Card className={`bg-gradient-to-br ${gradient} border-slate-700/50 backdrop-blur-sm overflow-hidden`}>
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-slate-400 text-xs mb-1">{label}</p>
            <p className="text-2xl font-bold text-white">{value}</p>
          </div>
          <div className={`${iconColor} opacity-80`}>
            {icon}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// بطاقة الرابط
function LinkCard({ link }: { link: LinkItem }) {
  const isWhatsapp = link.link_type === 'whatsapp'
  const date = new Date(link.created_at)
  const timeStr = date.toLocaleString('ar-SA', { 
    year: 'numeric', 
    month: 'short', 
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })

  return (
    <Card className={`mb-3 bg-slate-800/30 border-slate-700/50 backdrop-blur-sm hover:bg-slate-800/50 transition-all duration-300 overflow-hidden`}>
      <CardContent className="p-4">
        {/* الرأس */}
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className={`w-3 h-3 rounded-full ${isWhatsapp ? 'bg-green-500' : 'bg-blue-500'} shadow-lg ${isWhatsapp ? 'shadow-green-500/50' : 'shadow-blue-500/50'}`} />
            <Badge variant="outline" className={isWhatsapp ? 'border-green-500/30 text-green-400' : 'border-blue-500/30 text-blue-400'}>
              {isWhatsapp ? '🟢 واتساب' : '🔵 تيليجرام'}
            </Badge>
          </div>
          <div className="flex items-center gap-1 text-slate-500 text-xs">
            <Clock className="w-3 h-3" />
            {timeStr}
          </div>
        </div>

        {/* الرابط */}
        <a 
          href={link.link} 
          target="_blank" 
          rel="noopener noreferrer"
          className="block mb-3 group"
        >
          <div className="flex items-center gap-2 bg-slate-900/50 rounded-lg p-3 hover:bg-slate-900/80 transition-colors">
            <ExternalLink className="w-4 h-4 text-slate-400 group-hover:text-white shrink-0" />
            <span className="text-sm text-blue-300 group-hover:text-blue-200 truncate font-mono">
              {link.link}
            </span>
          </div>
        </a>

        {/* التفاصيل */}
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

        {/* النص الأصلي */}
        {link.message_text && (
          <div className="mt-3 bg-slate-900/40 rounded-lg p-3 text-xs text-slate-400 max-h-24 overflow-hidden">
            {link.message_text}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// بيانات تجريبية للعرض
const mockLinks: LinkItem[] = [
  {
    id: 1,
    link: 'https://chat.whatsapp.com/ABC123xyz',
    link_type: 'whatsapp',
    message_text: 'انضموا لمجموعة جامعة الأهلية',
    group_name: 'دعم SEU للطلاب الجدد',
    sender_name: 'محمد',
    sender_contact: '📱 +966500000000',
    source_phone: '+967770309310',
    message_link: 'https://t.me/c/123/456',
    created_at: new Date(Date.now() - 1000 * 60 * 5).toISOString()
  },
  {
    id: 2,
    link: 'https://t.me/kuwait_university',
    link_type: 'telegram',
    message_text: 'قناة جامعة الكويت الرسمية',
    group_name: 'مجموعة الكويت الجامعية',
    sender_name: 'أحمد',
    sender_contact: '✈️ @ahmed',
    source_phone: '+96550000000',
    message_link: 'https://t.me/c/123/457',
    created_at: new Date(Date.now() - 1000 * 60 * 30).toISOString()
  },
  {
    id: 3,
    link: 'https://chat.whatsapp.com/DEF456uvw',
    link_type: 'whatsapp',
    message_text: 'مجموعة جامعة قطر للطلاب',
    group_name: 'طلاب قطر',
    sender_name: 'سارة',
    sender_contact: '📱 +97430000000',
    source_phone: '+967770309310',
    message_link: 'https://t.me/c/123/458',
    created_at: new Date(Date.now() - 1000 * 60 * 60).toISOString()
  }
]

const mockStats: Stats = {
  total_links: 156,
  whatsapp_links: 98,
  telegram_links: 58,
  active_watchers: 7
}
