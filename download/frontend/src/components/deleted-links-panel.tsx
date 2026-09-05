'use client'

// ============================================================================
// [DELETED-LINKS] لوحة الروابط المحذوفة من قبل الإدارة
// Self-contained panel wired to the Render backend /api/deleted_links CRUD.
// No external hooks / no sonner / no radix-dialog / no radix-select — only
// the primitives already shipped with the real frontend (Card, Button,
// Input, Badge, Skeleton, framer-motion, lucide-react). Restore uses
// optimistic UI; purge uses native confirm. Inline toast via local state.
// ============================================================================

import * as React from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Copy,
  Plus,
  RotateCcw,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Constants & helpers
// ---------------------------------------------------------------------------

// [DASHBOARD-URL-v4.4.4] متوافق مع page.tsx — الخدمة الجديدة (القديمة معلّقة 503)
const API_URL: string =
  process.env.NEXT_PUBLIC_API_URL || 'https://whatsapp-userbot-alwh.onrender.com'

type DeletedReason =
  | 'spam'
  | 'duplicate'
  | 'off-topic'
  | 'scam'
  | 'policy-violation'
  | 'manual'

type LinkType = 'whatsapp' | 'telegram' | 'other'

const REASONS: DeletedReason[] = [
  'spam',
  'duplicate',
  'off-topic',
  'scam',
  'policy-violation',
  'manual',
]

const REASON_META: Record<DeletedReason, { label: string; className: string }> = {
  spam: {
    label: 'مزعج',
    className: 'bg-red-500/15 text-red-300 border-red-500/30',
  },
  duplicate: {
    label: 'مكرر',
    className: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  },
  scam: {
    label: 'احتيال',
    className: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
  },
  'off-topic': {
    label: 'خارج الموضوع',
    className: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
  },
  'policy-violation': {
    label: 'مخالفة سياسة',
    className: 'bg-orange-500/15 text-orange-300 border-orange-500/30',
  },
  manual: {
    label: 'حذف يدوي',
    className: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
  },
}

function reasonMeta(reason: string): { label: string; className: string } {
  if (reason && (REASONS as string[]).includes(reason)) {
    return REASON_META[reason as DeletedReason]
  }
  // Unknown reason → fallback to manual styling but show the raw reason label.
  return { label: reason || 'حذف', className: REASON_META.manual.className }
}

const LINK_TYPE_META: Record<LinkType, { label: string; color: string; ring: string; bg: string }> = {
  whatsapp: {
    label: 'واتساب',
    color: 'text-emerald-300',
    ring: 'ring-emerald-500/30',
    bg: 'bg-emerald-500/10',
  },
  telegram: {
    label: 'تيليجرام',
    color: 'text-sky-300',
    ring: 'ring-sky-500/30',
    bg: 'bg-sky-500/10',
  },
  other: {
    label: 'أخرى',
    color: 'text-slate-300',
    ring: 'ring-slate-500/30',
    bg: 'bg-slate-500/10',
  },
}

function linkTypeMeta(type: string): { label: string; color: string; ring: string; bg: string } {
  if (type === 'whatsapp') return LINK_TYPE_META.whatsapp
  if (type === 'telegram') return LINK_TYPE_META.telegram
  return LINK_TYPE_META.other
}

function initials(name: string): string {
  if (!name) return '؟'
  const cleaned = name.replace(/[^\p{L}\s]/gu, '').trim()
  const parts = cleaned.split(/\s+/)
  if (parts.length === 0 || !parts[0]) return '؟'
  if (parts.length === 1) return parts[0]!.slice(0, 2)
  return (parts[0]!.charAt(0) || '') + (parts[1]!.charAt(0) || '')
}

const ADMIN_GRADIENTS = [
  'from-emerald-500 to-teal-600',
  'from-sky-500 to-cyan-600',
  'from-violet-500 to-purple-600',
  'from-amber-500 to-orange-600',
  'from-rose-500 to-pink-600',
]

function adminGradient(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return ADMIN_GRADIENTS[hash % ADMIN_GRADIENTS.length]!
}

function formatArabicNumber(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('ar-EG').format(value)
}

function formatRelativeArabic(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  // "منذ X" — use Intl.RelativeTimeFormat ar with a coarse approximation.
  const diffMs = date.getTime() - Date.now()
  const diffSec = Math.round(diffMs / 1000)
  const absSec = Math.abs(diffSec)
  const rtf = new Intl.RelativeTimeFormat('ar', { numeric: 'auto' })
  if (absSec < 60) return rtf.format(Math.round(diffSec), 'second')
  const diffMin = Math.round(diffSec / 60)
  if (Math.abs(diffMin) < 60) return rtf.format(diffMin, 'minute')
  const diffHr = Math.round(diffMin / 60)
  if (Math.abs(diffHr) < 24) return rtf.format(diffHr, 'hour')
  const diffDay = Math.round(diffHr / 24)
  if (Math.abs(diffDay) < 30) return rtf.format(diffDay, 'day')
  const diffMo = Math.round(diffDay / 30)
  if (Math.abs(diffMo) < 12) return rtf.format(diffMo, 'month')
  return rtf.format(Math.round(diffMo / 12), 'year')
}

// ---------------------------------------------------------------------------
// Types — match the snake_case Render backend payload
// ---------------------------------------------------------------------------

interface DeletedLink {
  id: number
  original_link: string
  link_type: string
  source_group: string | null
  sender_name: string | null
  message_text: string | null
  deleted_by: string
  reason: string
  note: string | null
  deleted_at: string | null
  restored_at: string | null
  is_restored: number | boolean | null
}

interface DeletedStats {
  total: number
  by_admin: Record<string, number>
  by_reason: Record<string, number>
}

interface DeletedLinksResponse {
  links: DeletedLink[]
  stats: DeletedStats
  count?: number
}

// ---------------------------------------------------------------------------
// Toast — inline state-driven (no sonner installed in real frontend)
// ---------------------------------------------------------------------------

type ToastKind = 'success' | 'error' | 'info'
interface ToastMsg { id: number; kind: ToastKind; text: string; desc?: string }

function useToasts() {
  const [toasts, setToasts] = React.useState<ToastMsg[]>([])
  const counter = React.useRef(0)
  const dismiss = React.useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id))
  }, [])
  const push = React.useCallback((kind: ToastKind, text: string, desc?: string) => {
    const id = ++counter.current
    setToasts((t) => [...t, { id, kind, text, desc }])
    window.setTimeout(() => {
      setToasts((t) => t.filter((x) => x.id !== id))
    }, 3500)
  }, [])
  return { toasts, push, dismiss }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      <div className="relative">
        <div className="grid size-16 place-items-center rounded-full bg-slate-700/40">
          <Trash2 className="size-7 text-slate-400" />
        </div>
        <div className="absolute -bottom-1 -left-1 grid size-7 place-items-center rounded-full bg-slate-900 ring-2 ring-slate-600/50">
          <span className="text-slate-300 text-xs">✓</span>
        </div>
      </div>
      <div>
        <p className="font-medium text-slate-300">لا توجد روابط محذوفة</p>
        <p className="text-xs text-slate-500">
          عندما يحذف المشرف أو الإدمن رابطاً، سيظهر هنا.
        </p>
      </div>
    </div>
  )
}

function RowSkeleton() {
  return (
    <li className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-3">
      <div className="flex items-center gap-2">
        <Skeleton className="size-6 rounded-md bg-slate-700/60" />
        <Skeleton className="h-3 flex-1 bg-slate-700/60" />
        <Skeleton className="size-7 bg-slate-700/60" />
      </div>
      <div className="mt-2 flex items-center justify-between">
        <Skeleton className="h-3 w-1/3 bg-slate-700/60" />
        <Skeleton className="h-3 w-16 bg-slate-700/60" />
      </div>
      <div className="mt-2 flex items-center gap-1.5">
        <Skeleton className="size-5 rounded-full bg-slate-700/60" />
        <Skeleton className="h-3 w-12 bg-slate-700/60" />
        <Skeleton className="h-3 w-10 bg-slate-700/60" />
      </div>
    </li>
  )
}

function DeletedLinkRow({
  link,
  index,
  onRestored,
  onPurged,
  onToast,
  restoringId,
  purgingId,
}: {
  link: DeletedLink
  index: number
  onRestored: (id: number) => void
  onPurged: (id: number) => void
  onToast: (kind: ToastKind, text: string, desc?: string) => void
  restoringId: number | null
  purgingId: number | null
}) {
  const meta = linkTypeMeta(link.link_type)
  const reason = reasonMeta(link.reason)
  const isRestored = Boolean(link.is_restored)
  const isRestoring = restoringId === link.id
  const isPurging = purgingId === link.id

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(link.original_link || '')
      onToast('success', 'تم نسخ الرابط')
    } catch {
      onToast('error', 'تعذّر النسخ')
    }
  }

  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4, scale: 0.98 }}
      transition={{ duration: 0.25, delay: Math.min(index * 0.025, 0.2) }}
      className={cn(
        'group rounded-lg border bg-slate-900/30 p-3 transition-colors',
        isRestored
          ? 'border-emerald-500/40 bg-emerald-500/5'
          : 'border-slate-700/50 hover:bg-slate-800/40',
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'grid size-6 shrink-0 place-items-center rounded-md ring-1',
            meta.bg,
            meta.ring,
          )}
          aria-hidden
        >
          <span className={cn('text-[10px] font-bold', meta.color)}>
            {link.link_type === 'telegram' ? 'TG' : link.link_type === 'whatsapp' ? 'WA' : '—'}
          </span>
        </span>
        <code
          className="min-w-0 flex-1 truncate font-mono text-xs text-slate-200"
          dir="ltr"
          title={link.original_link}
        >
          {link.original_link}
        </code>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0 text-slate-400 hover:text-white"
          aria-label="نسخ الرابط"
          onClick={handleCopy}
        >
          <Copy className="size-3.5" />
        </Button>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span
          className="truncate font-medium text-slate-300"
          title={link.source_group || ''}
        >
          {link.source_group || 'غير معروف'}
        </span>
        <span className="text-slate-500">
          {link.sender_name || 'مرسِل غير معروف'}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
        <Badge
          variant="outline"
          className={cn('rounded-full px-1.5 py-0 text-[10px] border', reason.className)}
        >
          {reason.label}
        </Badge>
        <span className="flex items-center gap-1 rounded-full bg-slate-700/40 px-1.5 py-0.5">
          <span
            className={cn(
              'grid size-4 place-items-center rounded-full bg-gradient-to-br text-[8px] font-bold text-white',
              adminGradient(link.deleted_by || ''),
            )}
            aria-hidden
          >
            {initials(link.deleted_by || '')}
          </span>
          <span className="text-[10px] text-slate-200">{link.deleted_by}</span>
        </span>
        <span className="text-slate-500 tabular-nums">
          {formatRelativeArabic(link.deleted_at)}
        </span>
        {isRestored && (
          <Badge className="rounded-full bg-emerald-500/15 text-emerald-300 border-emerald-500/30 px-1.5 py-0 text-[10px]">
            ✓ تمت الاستعادة
          </Badge>
        )}
      </div>

      {link.note && (
        <p
          className="mt-1.5 line-clamp-2 truncate text-[11px] italic text-slate-400"
          title={link.note}
        >
          “{link.note}”
        </p>
      )}

      <div className="mt-2 flex items-center justify-end gap-1.5 border-t border-slate-700/50 pt-2">
        {isRestored ? (
          <span className="text-[10px] text-slate-500">
            {link.restored_at ? `استعيد · ${formatRelativeArabic(link.restored_at)}` : 'مستعادة'}
          </span>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2 text-[11px] text-emerald-300 hover:bg-emerald-500/10"
            disabled={isRestoring}
            onClick={() => onRestored(link.id)}
          >
            <RotateCcw className={cn('size-3.5', isRestoring && 'animate-spin')} />
            استعادة
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1 px-2 text-[11px] text-rose-300 hover:bg-rose-500/10"
          disabled={isPurging}
          onClick={() => {
            if (typeof window !== 'undefined' && window.confirm(`حذف السجل #${link.id} نهائياً؟`)) {
              onPurged(link.id)
            }
          }}
        >
          <Trash2 className="size-3.5" />
          حذف نهائي
        </Button>
      </div>
    </motion.li>
  )
}

// ---------------------------------------------------------------------------
// Recorder form — inline (no Dialog primitive available)
// ---------------------------------------------------------------------------

function RecorderForm({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: () => void
}) {
  const [form, setForm] = React.useState({
    original_link: '',
    link_type: 'telegram' as LinkType,
    source_group: '',
    sender_name: '',
    message_text: '',
    deleted_by: '',
    reason: 'manual' as DeletedReason,
    note: '',
  })
  const [submitting, setSubmitting] = React.useState(false)
  const [err, setErr] = React.useState<string | null>(null)

  const submit = async () => {
    setErr(null)
    if (!form.original_link || !form.source_group || !form.deleted_by) {
      setErr('الرجاء تعبئة الرابط والمجموعة واسم المحذف')
      return
    }
    setSubmitting(true)
    try {
      const res = await fetch(`${API_URL}/api/deleted_links`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          original_link: form.original_link.trim(),
          link_type: form.link_type,
          source_group: form.source_group.trim(),
          sender_name: form.sender_name.trim() || null,
          message_text: form.message_text.trim() || null,
          deleted_by: form.deleted_by.trim(),
          reason: form.reason,
          note: form.note.trim() || null,
        }),
      })
      if (!res.ok) {
        const txt = await res.text().catch(() => '')
        throw new Error(`${res.status} ${txt || res.statusText}`)
      }
      onCreated()
      onClose()
      setForm({
        original_link: '',
        link_type: 'telegram',
        source_group: '',
        sender_name: '',
        message_text: '',
        deleted_by: '',
        reason: 'manual',
        note: '',
      })
    } catch (e) {
      setErr(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return (
    <div className="rounded-lg border border-emerald-500/20 bg-slate-900/60 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-emerald-300">تسجيل رابط محذوف</h4>
        <Button variant="ghost" size="icon" className="size-7" onClick={onClose} aria-label="إغلاق">
          <X className="size-4" />
        </Button>
      </div>
      <div className="grid gap-2">
        <label className="text-[11px] text-slate-400">الرابط الأصلي</label>
        <Input
          dir="ltr"
          placeholder="https://t.me/..."
          value={form.original_link}
          onChange={(e) => setForm({ ...form, original_link: e.target.value })}
          className="bg-slate-800/50 border-slate-700 text-slate-100"
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="grid gap-2">
          <label className="text-[11px] text-slate-400">النوع</label>
          <select
            value={form.link_type}
            onChange={(e) => setForm({ ...form, link_type: e.target.value as LinkType })}
            className="h-9 rounded-md border border-slate-700 bg-slate-800/50 px-2 text-sm text-slate-100"
          >
            <option value="whatsapp">واتساب</option>
            <option value="telegram">تيليجرام</option>
            <option value="other">أخرى</option>
          </select>
        </div>
        <div className="grid gap-2">
          <label className="text-[11px] text-slate-400">السبب</label>
          <select
            value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value as DeletedReason })}
            className="h-9 rounded-md border border-slate-700 bg-slate-800/50 px-2 text-sm text-slate-100"
          >
            {REASONS.map((r) => (
              <option key={r} value={r}>
                {reasonMeta(r).label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="grid gap-2">
        <label className="text-[11px] text-slate-400">المجموعة المصدرية</label>
        <Input
          placeholder="اسم المجموعة"
          value={form.source_group}
          onChange={(e) => setForm({ ...form, source_group: e.target.value })}
          className="bg-slate-800/50 border-slate-700 text-slate-100"
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="grid gap-2">
          <label className="text-[11px] text-slate-400">اسم المُرسِل</label>
          <Input
            placeholder="مرسِل غير معروف"
            value={form.sender_name}
            onChange={(e) => setForm({ ...form, sender_name: e.target.value })}
            className="bg-slate-800/50 border-slate-700 text-slate-100"
          />
        </div>
        <div className="grid gap-2">
          <label className="text-[11px] text-slate-400">اسم المحذف</label>
          <Input
            placeholder="المشرف-عزام"
            value={form.deleted_by}
            onChange={(e) => setForm({ ...form, deleted_by: e.target.value })}
            className="bg-slate-800/50 border-slate-700 text-slate-100"
          />
        </div>
      </div>
      <div className="grid gap-2">
        <label className="text-[11px] text-slate-400">ملاحظة الإدارة</label>
        <Input
          placeholder="سبب إضافي أو توضيح..."
          value={form.note}
          onChange={(e) => setForm({ ...form, note: e.target.value })}
          className="bg-slate-800/50 border-slate-700 text-slate-100"
        />
      </div>
      {err && (
        <p className="text-[11px] text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded px-2 py-1">
          {err}
        </p>
      )}
      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
          إلغاء
        </Button>
        <Button size="sm" onClick={submit} disabled={submitting}>
          {submitting ? 'جارٍ التسجيل…' : 'تسجيل الحذف'}
        </Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function DeletedLinksPanel() {
  const [search, setSearch] = React.useState('')
  const [admin, setAdmin] = React.useState('')
  const [reason, setReason] = React.useState('')
  const [restoredOnly, setRestoredOnly] = React.useState(false)
  const [data, setData] = React.useState<DeletedLinksResponse | null>(null)
  const [isLoading, setIsLoading] = React.useState(true)
  const [isFetching, setIsFetching] = React.useState(false)
  const [isError, setIsError] = React.useState(false)
  const [recorderOpen, setRecorderOpen] = React.useState(false)
  const [restoringId, setRestoringId] = React.useState<number | null>(null)
  const [purgingId, setPurgingId] = React.useState<number | null>(null)
  const { toasts, push: pushToast, dismiss } = useToasts()
  const fetchSeq = React.useRef(0)

  const fetchLinks = React.useCallback(async () => {
    const seq = ++fetchSeq.current
    setIsFetching(true)
    try {
      const sp = new URLSearchParams()
      if (admin) sp.set('admin', admin)
      if (reason) sp.set('reason', reason)
      if (search) sp.set('search', search)
      if (restoredOnly) sp.set('restored', '1')
      const qs = sp.toString()
      const url = `${API_URL}/api/deleted_links${qs ? `?${qs}` : ''}`
      const res = await fetch(url, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const json = (await res.json()) as DeletedLinksResponse
      if (seq === fetchSeq.current) {
        setData(json)
        setIsError(false)
        setIsLoading(false)
      }
    } catch {
      if (seq === fetchSeq.current) {
        setIsError(true)
        setIsLoading(false)
      }
    } finally {
      if (seq === fetchSeq.current) setIsFetching(false)
    }
  }, [admin, reason, search, restoredOnly])

  React.useEffect(() => {
    fetchLinks()
    const id = window.setInterval(fetchLinks, 30_000)
    return () => window.clearInterval(id)
  }, [fetchLinks])

  const links = data?.links ?? []
  const stats = data?.stats

  const distinctAdmins = React.useMemo(
    () => Object.keys(stats?.by_admin ?? {}).sort(),
    [stats],
  )

  const todayCount = React.useMemo(() => {
    if (!links.length) return 0
    const startOfDay = new Date()
    startOfDay.setHours(0, 0, 0, 0)
    return links.filter((l) => {
      const d = new Date(l.deleted_at || '')
      return !Number.isNaN(d.getTime()) && d >= startOfDay
    }).length
  }, [links])

  const currentAdminCount = admin ? (stats?.by_admin[admin] ?? 0) : (stats?.total ?? 0)

  const handleRestored = React.useCallback(
    async (id: number) => {
      // optimistic
      setData((prev) =>
        prev
          ? {
              ...prev,
              links: prev.links.map((l) =>
                l.id === id
                  ? { ...l, is_restored: 1, restored_at: new Date().toISOString() }
                  : l,
              ),
            }
          : prev,
      )
      setRestoringId(id)
      try {
        const res = await fetch(`${API_URL}/api/deleted_links/${id}/restore`, {
          method: 'POST',
          headers: { Accept: 'application/json' },
        })
        if (!res.ok) {
          const txt = await res.text().catch(() => '')
          throw new Error(`${res.status} ${txt || res.statusText}`)
        }
        pushToast('success', 'تمت استعادة الرابط')
        await fetchLinks()
      } catch (e) {
        pushToast('error', 'تعذّرت الاستعادة', String(e))
        await fetchLinks()
      } finally {
        setRestoringId(null)
      }
    },
    [fetchLinks, pushToast],
  )

  const handlePurged = React.useCallback(
    async (id: number) => {
      // optimistic
      setData((prev) =>
        prev
          ? {
              ...prev,
              links: prev.links.filter((l) => l.id !== id),
              stats: {
                ...prev.stats,
                total: Math.max(0, prev.stats.total - 1),
              },
            }
          : prev,
      )
      setPurgingId(id)
      try {
        const res = await fetch(`${API_URL}/api/deleted_links/${id}`, {
          method: 'DELETE',
          headers: { Accept: 'application/json' },
        })
        if (!res.ok) {
          const txt = await res.text().catch(() => '')
          throw new Error(`${res.status} ${txt || res.statusText}`)
        }
        pushToast('success', 'تم حذف السجل نهائياً')
        await fetchLinks()
      } catch (e) {
        pushToast('error', 'تعذّر الحذف', String(e))
        await fetchLinks()
      } finally {
        setPurgingId(null)
      }
    },
    [fetchLinks, pushToast],
  )

  const resetFilters = () => {
    setSearch('')
    setAdmin('')
    setReason('')
    setRestoredOnly(false)
  }

  return (
    <Card className="relative overflow-hidden border-emerald-500/30 bg-slate-800/30 backdrop-blur-sm">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-emerald-500/60 via-teal-500/60 to-emerald-500/60" />
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="text-xl" aria-hidden>🚮</span>
              <CardTitle className="text-base font-bold text-slate-100 sm:text-lg">
                لوحة الروابط المحذوفة من قبل الإدارة
              </CardTitle>
              <Badge className="rounded-full bg-emerald-500/15 text-emerald-300 border-emerald-500/30">
                {stats?.total ?? 0}
              </Badge>
            </div>
            <CardDescription className="text-slate-400">
              الروابط التي سحبها/حذفها المشرف أو الإدمن
            </CardDescription>
          </div>
          <Button
            size="sm"
            variant="default"
            className="gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white"
            onClick={() => setRecorderOpen((v) => !v)}
          >
            <Plus className="size-4" />
            تسجيل حذف
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {recorderOpen && (
          <RecorderForm
            open={recorderOpen}
            onClose={() => setRecorderOpen(false)}
            onCreated={() => {
              pushToast('success', 'تم تسجيل الحذف')
              fetchLinks()
            }}
          />
        )}

        {/* Mini stats */}
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-lg border border-slate-700/50 bg-slate-900/40 p-2.5 text-center">
            <div className="text-lg font-bold tabular-nums text-slate-100">
              {formatArabicNumber(stats?.total ?? 0)}
            </div>
            <div className="text-[10px] text-slate-500">إجمالي المحذوفات</div>
          </div>
          <div className="rounded-lg border border-slate-700/50 bg-slate-900/40 p-2.5 text-center">
            <div className="text-lg font-bold tabular-nums text-slate-100">
              {formatArabicNumber(currentAdminCount)}
            </div>
            <div className="truncate text-[10px] text-slate-500" title={admin}>
              {admin ? admin : 'كل المحذفين'}
            </div>
          </div>
          <div className="rounded-lg border border-slate-700/50 bg-slate-900/40 p-2.5 text-center">
            <div className="text-lg font-bold tabular-nums text-slate-100">
              {formatArabicNumber(todayCount)}
            </div>
            <div className="text-[10px] text-slate-500">حذفات اليوم</div>
          </div>
        </div>

        {/* Filter toolbar */}
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-[1.5fr_1fr_1fr_auto]">
          <div className="relative">
            <Search className="absolute top-1/2 right-2 size-4 -translate-y-1/2 text-slate-500" />
            <Input
              placeholder="ابحث برابط أو مجموعة..."
              className="bg-slate-800/50 border-slate-700 text-slate-100 placeholder:text-slate-500 pr-8"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="بحث"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-slate-500 hover:text-slate-200"
                aria-label="مسح البحث"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
          <select
            value={admin || 'all'}
            onChange={(e) => setAdmin(e.target.value === 'all' ? '' : e.target.value)}
            className="h-9 w-full rounded-md border border-slate-700 bg-slate-800/50 px-2 text-sm text-slate-100"
            aria-label="فلتر المحذف"
          >
            <option value="all">كل المحذفين</option>
            {distinctAdmins.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <select
            value={reason || 'all'}
            onChange={(e) => setReason(e.target.value === 'all' ? '' : e.target.value)}
            className="h-9 w-full rounded-md border border-slate-700 bg-slate-800/50 px-2 text-sm text-slate-100"
            aria-label="فلتر السبب"
          >
            <option value="all">كل الأسباب</option>
            {REASONS.map((r) => (
              <option key={r} value={r}>
                {reasonMeta(r).label}
              </option>
            ))}
          </select>
          <label className="flex cursor-pointer items-center gap-2 rounded-md border border-slate-700 bg-slate-800/40 px-3 py-2 text-xs text-slate-200">
            <input
              type="checkbox"
              checked={restoredOnly}
              onChange={(e) => setRestoredOnly(e.target.checked)}
              className="size-4 accent-emerald-500"
            />
            عرض المستعادة
          </label>
        </div>

        {(search || admin || reason || restoredOnly) && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-500">الفلاتر النشطة:</span>
            {search && (
              <Badge variant="secondary" className="gap-1 rounded-full bg-slate-700/50 text-slate-200">
                بحث: {search}
                <button onClick={() => setSearch('')} aria-label="إزالة">
                  <X className="size-3" />
                </button>
              </Badge>
            )}
            {admin && (
              <Badge variant="secondary" className="gap-1 rounded-full bg-slate-700/50 text-slate-200">
                محذف: {admin}
                <button onClick={() => setAdmin('')} aria-label="إزالة">
                  <X className="size-3" />
                </button>
              </Badge>
            )}
            {reason && (
              <Badge variant="secondary" className="gap-1 rounded-full bg-slate-700/50 text-slate-200">
                سبب: {reasonMeta(reason).label}
                <button onClick={() => setReason('')} aria-label="إزالة">
                  <X className="size-3" />
                </button>
              </Badge>
            )}
            {restoredOnly && (
              <Badge variant="secondary" className="gap-1 rounded-full bg-slate-700/50 text-slate-200">
                المستعادة فقط
                <button onClick={() => setRestoredOnly(false)} aria-label="إزالة">
                  <X className="size-3" />
                </button>
              </Badge>
            )}
            <button
              onClick={resetFilters}
              className="text-xs text-emerald-300 hover:underline"
            >
              مسح الكل
            </button>
          </div>
        )}

        {/* Inline toast stack */}
        {toasts.length > 0 && (
          <div className="fixed bottom-4 left-4 z-50 flex flex-col gap-2">
            {toasts.map((t) => (
              <div
                key={t.id}
                className={cn(
                  'flex items-start gap-2 rounded-md border px-3 py-2 text-xs shadow-lg backdrop-blur-sm max-w-xs',
                  t.kind === 'success' && 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
                  t.kind === 'error' && 'border-rose-500/40 bg-rose-500/10 text-rose-200',
                  t.kind === 'info' && 'border-slate-500/40 bg-slate-700/40 text-slate-200',
                )}
                role="status"
              >
                <span className="flex-1">
                  {t.text}
                  {t.desc && <span className="block text-[10px] opacity-70">{t.desc}</span>}
                </span>
                <button
                  onClick={() => dismiss(t.id)}
                  className="shrink-0 text-slate-400 hover:text-white"
                  aria-label="إغلاق"
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* List */}
        {isError ? (
          <div
            role="alert"
            className="flex flex-col items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-500/5 p-4 text-center text-rose-300"
          >
            <p className="text-sm font-medium">تعذّر تحميل لوحة المحذوفات</p>
            <Button variant="outline" size="sm" onClick={() => fetchLinks()}>
              إعادة المحاولة
            </Button>
          </div>
        ) : (
          <ul className="max-h-96 space-y-1.5 overflow-y-auto pr-1 modal-scroll">
            <AnimatePresence mode="popLayout">
              {isLoading
                ? Array.from({ length: 4 }).map((_, i) => <RowSkeleton key={i} />)
                : links.length === 0
                  ? <li key="empty"><EmptyState /></li>
                  : links.map((l, i) => (
                      <DeletedLinkRow
                        key={l.id}
                        link={l}
                        index={i}
                        onRestored={handleRestored}
                        onPurged={handlePurged}
                        onToast={pushToast}
                        restoringId={restoringId}
                        purgingId={purgingId}
                      />
                    ))}
            </AnimatePresence>
          </ul>
        )}
        {!isLoading && !isError && links.length > 0 && (
          <div className="flex items-center justify-between border-t border-slate-700/50 pt-3 text-xs text-slate-500">
            <span>
              عرض {formatArabicNumber(links.length)} من {formatArabicNumber(stats?.total ?? 0)} سجل
            </span>
            <button
              onClick={() => fetchLinks()}
              className={cn(
                'text-emerald-300 hover:underline',
                isFetching && 'opacity-50',
              )}
              disabled={isFetching}
            >
              {isFetching ? 'جارٍ التحديث…' : 'تحديث'}
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
