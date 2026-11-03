<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card budget-card">
        <div class="budget-row">
          <label class="budget-label" for="restock-budget">{{ t('restocking.budget') }}</label>
          <span class="budget-value">{{ formatMoney(budget) }}</span>
        </div>
        <input
          id="restock-budget"
          v-model.number="budget"
          class="budget-slider"
          type="range"
          min="0"
          :max="maxBudget"
          :step="BUDGET_STEP"
        />
        <div class="budget-scale">
          <span>{{ formatMoney(0) }}</span>
          <span>{{ formatMoney(maxBudget) }}</span>
        </div>
        <p class="budget-hint">{{ t('restocking.budgetHint') }}</p>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ formatMoney(budget) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.plannedSpend') }}</div>
          <div class="stat-value">{{ formatMoney(plannedSpend) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ formatMoney(remainingBudget) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.items') }}</div>
          <div class="stat-value">{{ recommendations.length }}</div>
        </div>
      </div>

      <div v-if="submitError" class="error">{{ submitError }}</div>
      <div v-if="placedOrder" class="success-banner" role="status">
        <span>
          {{ t('restocking.success', {
            orderNumber: placedOrder.order_number,
            days: placedOrder.lead_time_days,
            date: formatDate(placedOrder.expected_delivery)
          }) }}
        </span>
        <router-link to="/orders" class="success-link">{{ t('restocking.viewOrders') }}</router-link>
      </div>

      <div class="card">
        <div class="card-header header-with-action">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }} ({{ recommendations.length }})</h3>
          <button
            type="button"
            class="place-order-btn"
            :disabled="recommendations.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="recommendations.length === 0" class="empty-state">
          {{ t('restocking.empty') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.name') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.currentStock') }}</th>
                <th>{{ t('restocking.table.forecastDemand') }}</th>
                <th>{{ t('restocking.table.shortfall') }}</th>
                <th>{{ t('restocking.table.orderQty') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineCost') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rec in recommendations" :key="rec.sku">
                <td><strong>{{ rec.sku }}</strong></td>
                <td>{{ translateProductName(rec.name) }}</td>
                <td>
                  <span :class="['badge', rec.trend]">{{ t(`trends.${rec.trend}`) }}</span>
                </td>
                <td>{{ rec.quantity_on_hand }}</td>
                <td>{{ rec.forecasted_demand }}</td>
                <td>{{ rec.shortfall }}</td>
                <td><strong>{{ rec.order_quantity }}</strong></td>
                <td>{{ formatMoney(rec.unit_cost) }}</td>
                <td><strong>{{ formatMoney(rec.line_cost) }}</strong></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'
import { formatCurrency } from '../utils/currency'

// Slider granularity in USD (the budget is always held in USD)
const BUDGET_STEP = 1000

export default {
  name: 'Restocking',
  setup() {
    const { t, currentLocale, currentCurrency, translateProductName } = useI18n()
    const { selectedLocation, selectedCategory, getCurrentFilters } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const forecasts = ref([])
    const inventoryItems = ref([])
    const budget = ref(0)
    // Only seed the default budget on the first load so later filter reloads keep the user's choice
    let budgetInitialised = false
    // Guards against an older, slower request overwriting a newer one after rapid filter changes
    let loadToken = 0

    const submitting = ref(false)
    const submitError = ref(null)
    const placedOrder = ref(null)

    const formatMoney = (amount) => formatCurrency(amount, currentCurrency.value)

    const formatDate = (dateString) => {
      const date = new Date(dateString)
      if (isNaN(date.getTime())) return '-'
      return date.toLocaleDateString(currentLocale.value === 'ja' ? 'ja-JP' : 'en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      })
    }

    // Join forecasts to inventory by SKU. Forecasts whose SKU was filtered out of
    // inventory (warehouse/category filters) are dropped.
    const candidates = computed(() => {
      const bySku = new Map(inventoryItems.value.map(item => [item.sku, item]))
      const result = []
      for (const f of forecasts.value) {
        const inv = bySku.get(f.item_sku)
        if (!inv || !(inv.unit_cost > 0)) continue
        result.push({
          sku: inv.sku,
          name: inv.name,
          trend: f.trend,
          quantity_on_hand: inv.quantity_on_hand,
          forecasted_demand: f.forecasted_demand,
          unit_cost: inv.unit_cost,
          shortfall: Math.max(0, f.forecasted_demand - inv.quantity_on_hand)
        })
      }
      return result
    })

    // Slider max = cost of fully covering every shortfall, rounded up to the step (at least one step)
    const maxBudget = computed(() => {
      const total = candidates.value.reduce((sum, c) => sum + c.shortfall * c.unit_cost, 0)
      return Math.max(BUDGET_STEP, Math.ceil(total / BUDGET_STEP) * BUDGET_STEP)
    })

    // Keep the budget within the slider range when the range shrinks (e.g. filters change)
    watch(maxBudget, (max) => {
      if (budget.value > max) budget.value = max
    })

    const recommendations = computed(() => {
      // Biggest shortfall first; on ties, items with increasing demand take priority
      const sorted = candidates.value
        .filter(c => c.shortfall > 0)
        .sort((a, b) => {
          if (b.shortfall !== a.shortfall) return b.shortfall - a.shortfall
          return (b.trend === 'increasing') - (a.trend === 'increasing')
        })

      // Greedy fill: walk the priority list and buy as much of each shortfall as the
      // remaining budget affords (whole units only). Items that cannot afford a single
      // unit are skipped, but cheaper later items may still fit. Never exceeds the budget.
      let remaining = budget.value
      const lines = []
      for (const item of sorted) {
        const qty = Math.min(item.shortfall, Math.floor(remaining / item.unit_cost))
        if (qty <= 0) continue
        const lineCost = qty * item.unit_cost
        remaining -= lineCost
        lines.push({ ...item, order_quantity: qty, line_cost: lineCost })
      }
      return lines
    })

    const plannedSpend = computed(() => recommendations.value.reduce((sum, r) => sum + r.line_cost, 0))
    const remainingBudget = computed(() => Math.max(0, budget.value - plannedSpend.value))

    const loadData = async () => {
      const token = ++loadToken
      try {
        loading.value = true
        error.value = null
        const filters = getCurrentFilters()
        // Only warehouse and category apply here; period and status are not relevant to restocking
        const [forecastData, inventoryData] = await Promise.all([
          api.getDemandForecasts(),
          api.getInventory({ warehouse: filters.warehouse, category: filters.category })
        ])
        if (token !== loadToken) return
        forecasts.value = forecastData
        inventoryItems.value = inventoryData
        if (!budgetInitialised) {
          budget.value = Math.floor(maxBudget.value / 2 / BUDGET_STEP) * BUDGET_STEP
          budgetInitialised = true
        }
      } catch (err) {
        if (token !== loadToken) return
        error.value = t('restocking.loadError') + ': ' + err.message
      } finally {
        if (token === loadToken) loading.value = false
      }
    }

    watch([selectedLocation, selectedCategory], loadData)

    const placeOrder = async () => {
      if (submitting.value || recommendations.value.length === 0) return
      submitting.value = true
      submitError.value = null
      placedOrder.value = null
      try {
        // Server prices lines from its own inventory unit_cost; only sku and quantity are sent
        placedOrder.value = await api.createRestockOrder({
          budget: budget.value,
          lines: recommendations.value.map(r => ({ sku: r.sku, quantity: r.order_quantity }))
        })
      } catch (err) {
        const detail = err.response && err.response.data && err.response.data.detail
        // detail can be an array for framework validation errors; only show plain text
        submitError.value = typeof detail === 'string' && detail ? detail : t('restocking.submitError')
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadData)

    return {
      t,
      BUDGET_STEP,
      loading,
      error,
      budget,
      maxBudget,
      recommendations,
      plannedSpend,
      remainingBudget,
      submitting,
      submitError,
      placedOrder,
      formatMoney,
      formatDate,
      placeOrder,
      translateProductName
    }
  }
}
</script>

<style scoped>
.budget-card {
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}

.budget-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.budget-slider {
  width: 100%;
  accent-color: #334155;
  cursor: pointer;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: #64748b;
}

.budget-hint {
  margin: 0.75rem 0 0;
  font-size: 0.813rem;
  color: #64748b;
}

.header-with-action {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.place-order-btn {
  background: #0f172a;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
}

.place-order-btn:hover:not(:disabled) {
  background: #1e293b;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
}

.success-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
  padding: 1rem;
  border-radius: 8px;
  margin: 0 0 1.5rem;
  font-size: 0.938rem;
}

.success-link {
  color: #166534;
  font-weight: 600;
  white-space: nowrap;
}
</style>
