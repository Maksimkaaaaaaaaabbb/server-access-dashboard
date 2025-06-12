<script setup>
import { ref, onMounted, computed, watch, onBeforeUnmount, nextTick } from 'vue'
import axios from 'axios'
import CountryFlag from 'vue-country-flag-next'

// --- State for configuration ---
const apiBaseUrl = ref('')
const apiKey = ref('')
const configLoading = ref(true)
const configError = ref(null)
const configSource = ref(null)

// --- Axios instance (created after loading config) ---
let apiClient = null

// --- Data & Loading States ---
const logs = ref([])
const summary = ref([])
const loading = ref(false) // General loading state for GET requests
const isCollectingAndRefreshing = ref(false) // For the entire "Collect & Refresh" button process
const isFilteringViaButtonClick = ref(false)
const showDelayedFilterButtonLoading = ref(false)
let filterButtonLoadingTimer = null
const filterButtonLoadingDelay = 500

const error = ref(null)
const lastUpdateTime = ref(null)

// --- Filtering & Sorting States ---
const filterIp = ref('')
const filterCountry = ref('')
const filterDomain = ref('')
const filterStatusCode = ref(null)
const sortKey = ref('timestamp')
const sortDirection = ref('desc')

// --- Auto-Update States ---
const autoUpdateEnabled = ref(false)
const autoUpdateInterval = ref(30)
let autoUpdateIntervalId = null

// --- Pagination States ---
const currentPage = ref(1)
const itemsPerPage = ref(15)
const totalItems = ref(0)

// --- Polling States ---
let pollingIntervalId = null
const pollingIntervalMs = 2000
const pollingTimeoutMs = 300000 // 5 Minute Timeout

// --- UI State ---
const isDarkMode = ref(
  localStorage.getItem('darkMode') === 'true' ||
    (!('darkMode' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches),
)
// --- State for Ripple-effect ---
const rippleActive = ref(false)
const rippleX = ref(0)
const rippleY = ref(0)

// --- Computed Properties ---
const totalPages = computed(() => {
  return itemsPerPage.value > 0 ? Math.ceil(totalItems.value / itemsPerPage.value) : 0
})
const skipValue = computed(() => {
  return (currentPage.value - 1) * itemsPerPage.value
})
const formattedLastUpdateTime = computed(() => {
  if (!lastUpdateTime.value) return null
  return lastUpdateTime.value.toLocaleTimeString('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
})

// --- Creates the Axios instance ---
function createApiClient() {
  if (!apiBaseUrl.value) {
    console.error('Cannot create apiClient: apiBaseUrl is missing.')
    return null
  }
  if (!apiKey.value) {
    console.warn('Creating apiClient without API Key.')
  }
  return axios.create({
    baseURL: apiBaseUrl.value,
    headers: { 'X-API-Key': apiKey.value || '' },
  })
}

// --- Loads configuration from /config.json ---
async function loadConfigFromJson() {
  configLoading.value = true
  configError.value = null
  apiClient = null
  try {
    console.log('Trying to load configuration from /config.json...')
    const response = await fetch('/config.json')
    const contentType = response.headers.get('content-type')
    if (!response.ok || !contentType || !contentType.includes('application/json')) {
      let errorBody = ''
      try {
        errorBody = await response.text()
      } catch (e) {
        /* ignore */
      }
      throw new Error(
        `Configuration file '/config.json' could not be loaded or parsed. Status: ${response.status}. Body: ${errorBody}`,
      )
    }
    const config = await response.json()
    console.log('Loaded configuration from JSON:', config)
    if (!config || typeof config.apiUrl !== 'string' || !config.apiUrl) {
      throw new Error("'apiUrl' not found as a valid string in config.json.")
    }
    if (!config || typeof config.apiKey !== 'string') {
      console.warn("'apiKey' not found as a valid string in config.json.")
      apiKey.value = ''
    } else {
      apiKey.value = config.apiKey
    }
    apiBaseUrl.value = config.apiUrl
    configSource.value = 'json'
    console.log('API Base URL (json):', apiBaseUrl.value)
    console.log('API Key (json):', apiKey.value ? 'Yes' : 'No (empty)')
    apiClient = createApiClient()
    if (!apiClient) throw new Error('Error creating API client after loading JSON config.')
  } catch (err) {
    console.error('Error loading JSON configuration:', err)
    configError.value = `Error loading backend configuration from /config.json: ${err.message}.`
    apiBaseUrl.value = ''
    apiKey.value = ''
  } finally {
    configLoading.value = false
  }
}

// --- Fetch Logs and Summary Data (GET Requests) ---
async function fetchData(triggeredByButton = false) {
  if (configError.value || !apiClient) {
    if (!configError.value) {
      error.value = 'API Client not initialized.'
    }
    return
  }

  loading.value = true
  if (triggeredByButton) {
    isFilteringViaButtonClick.value = true
    showDelayedFilterButtonLoading.value = false
    clearTimeout(filterButtonLoadingTimer)
    filterButtonLoadingTimer = setTimeout(() => {
      if (isFilteringViaButtonClick.value) {
        console.log('Filter button loading delay reached.')
        showDelayedFilterButtonLoading.value = true
      }
    }, filterButtonLoadingDelay)
  } else {
    showDelayedFilterButtonLoading.value = false
    clearTimeout(filterButtonLoadingTimer)
  }

  let fetchSuccessful = false
  const params = {
    limit: itemsPerPage.value,
    skip: skipValue.value,
    ip_address: filterIp.value || null,
    country: filterCountry.value || null,
    domain: filterDomain.value || null,
    status_code: filterStatusCode.value !== null ? filterStatusCode.value : null,
    sort_by: sortKey.value,
    sort_dir: sortDirection.value,
  }
  const filteredParams = Object.fromEntries(Object.entries(params).filter(([_, v]) => v !== null))

  try {
    const logsPromise = apiClient.get('/logs/', { params: filteredParams })
    const summaryPromise = apiClient.get('/logs/summary/by-country/')
    const [logsResult, summaryResult] = await Promise.allSettled([logsPromise, summaryPromise])
    let currentError = null

    // Process logs response
    if (logsResult.status === 'fulfilled' && logsResult.value.data?.total_count !== undefined) {
      logs.value = logsResult.value.data.logs || []
      totalItems.value = logsResult.value.data.total_count
      fetchSuccessful = true
    } else if (logsResult.status === 'rejected') {
      const err = logsResult.reason
      currentError = `API error (Logs): ${err.response?.status || 'Network'}.`
      logs.value = []
      totalItems.value = 0
    } else {
      currentError = 'Unexpected response (Logs).'
      logs.value = []
      totalItems.value = 0
    }
    // Process summary response
    if (summaryResult.status === 'fulfilled' && Array.isArray(summaryResult.value.data)) {
      summary.value = summaryResult.value.data
      fetchSuccessful = true
    } else if (summaryResult.status === 'rejected') {
      const err = summaryResult.reason
      let summaryError = `API error (Summary): ${err.response?.status || 'Network'}.`
      currentError = currentError ? `${currentError} | ${summaryError}` : summaryError
      summary.value = []
    } else {
      let summaryError = 'Unexpected response (Summary).'
      currentError = currentError ? `${currentError} | ${summaryError}` : summaryError
      summary.value = []
    }

    if (currentError) {
      error.value = currentError
    } else if (fetchSuccessful) {
      error.value = null
      lastUpdateTime.value = new Date()
    }
  } catch (err) {
    console.error('fetchData: General fetch error:', err)
    error.value = `General error fetching data: ${err.message}`
    logs.value = []
    summary.value = []
    totalItems.value = 0
  } finally {
    loading.value = false
    isFilteringViaButtonClick.value = false
    clearTimeout(filterButtonLoadingTimer)
    showDelayedFilterButtonLoading.value = false
  }
}

// --- Function to check the collection status ---
async function checkCollectionStatus() {
  if (!apiClient) return 'error'
  try {
    console.log('Polling: Checking status...')
    const response = await apiClient.get('/collect-logs/status')
    const status = response.data?.status
    console.log('Polling: Status received:', status)
    return status
  } catch (err) {
    console.error('Polling: Error while checking status:', err)
    error.value = `Error while checking collection status: ${err.message}`
    return 'error'
  }
}

// --- Collect Logs (POST), Poll Status, and Refresh Data ---
async function collectAndRefreshLogs() {
  if (configError.value || !apiClient) {
    error.value = configError.value || 'API client not initialized.'
    return
  }
  if (isCollectingAndRefreshing.value) {
    console.warn('Collection already in progress.')
    return
  }

  isCollectingAndRefreshing.value = true
  error.value = null
  stopPolling()

  const collectUrl = `/collect-logs/`
  let pollingTimeoutId = null

  try {
    console.log('API Collect Logs URL:', apiClient.defaults.baseURL + collectUrl)
    const postResponse = await apiClient.post(collectUrl)
    console.log('Collect logs POST response:', postResponse.data)

    if (postResponse.status === 202 && postResponse.data?.message?.includes('started')) {
      console.log('Collection started, starting polling...')
      pollingIntervalId = setInterval(async () => {
        const status = await checkCollectionStatus()
        if (status === 'finished') {
          console.log('Polling: Collection finished. Stopping polling and refreshing data.')
          stopPolling()
          clearTimeout(pollingTimeoutId)
          await fetchData(false)
          isCollectingAndRefreshing.value = false
        } else if (status === 'error') {
          console.error('Polling: Collection reported an error. Stopping polling.')
          stopPolling()
          clearTimeout(pollingTimeoutId)
          error.value = error.value || 'Error during log collection in backend.'
          isCollectingAndRefreshing.value = false
        } else if (status === 'idle') {
          console.warn("Polling: Status is 'idle'. Stopping polling.")
          stopPolling()
          clearTimeout(pollingTimeoutId)
          await fetchData(false)
          isCollectingAndRefreshing.value = false
        }
      }, pollingIntervalMs)

      pollingTimeoutId = setTimeout(() => {
        console.error('Polling: Timeout reached! Stopping polling.')
        stopPolling()
        error.value = 'Timeout while waiting for log collection.'
        isCollectingAndRefreshing.value = false
      }, pollingTimeoutMs)
    } else {
      console.warn('Log collection not started or already running:', postResponse.data?.message)
      if (postResponse.status !== 202) {
        error.value = `Error starting collection: ${postResponse.status} - ${postResponse.data?.detail || postResponse.data?.message || 'Unknown error'}`
      } else if (postResponse.data?.message?.includes('already running')) {
        error.value = 'Log collection is already running.'
      }
      isCollectingAndRefreshing.value = false
    }
  } catch (err) {
    console.error('collectAndRefreshLogs: Error during POST request:', err)
    if (axios.isAxiosError(err)) {
      if (err.response?.status === 401 || err.response?.status === 403) {
        error.value = `API Auth Error (Collect): ${err.response.status} - ${err.response.data?.detail || 'Key invalid/missing'}.`
      } else if (err.response?.status === 409) {
        error.value = 'Log collection is already running (reported by server).'
      } else if (err.response) {
        error.value = `API Error (Collect): ${err.response.status}.`
      } else if (err.request) {
        error.value = `No response (Collect) from ${apiClient?.defaults?.baseURL}.`
      } else {
        error.value = `Request Error (Collect): ${err.message}`
      }
    } else {
      error.value = `General error: ${err.message}`
    }
    isCollectingAndRefreshing.value = false
    stopPolling()
    clearTimeout(pollingTimeoutId)
  }
}
// --- Function to stop polling ---
function stopPolling() {
  if (pollingIntervalId) {
    clearInterval(pollingIntervalId)
    pollingIntervalId = null
    console.log('Polling stopped.')
  }
}
// --- Remaining Functions (Sort, Filter, Pagination, AutoUpdate, Format, DarkMode) ---
function sortBy(key) {
  if (sortKey.value === key) {
    sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDirection.value = key === 'timestamp' ? 'desc' : 'asc'
  }
  currentPage.value = 1
  fetchData(false) // triggeredByButton = false
}
function applyFilters(event) {
  if (loading.value || isCollectingAndRefreshing.value) {
    console.log('Apply filters ignored while loading.')
    return
  }
  if (event) {
    const button = event.currentTarget
    const rect = button.getBoundingClientRect()
    rippleX.value = event.clientX - rect.left
    rippleY.value = event.clientY - rect.top
    rippleActive.value = false
    nextTick(() => {
      rippleActive.value = true
      setTimeout(() => {
        rippleActive.value = false
      }, 600)
    })
  }
  currentPage.value = 1
  fetchData(true) // triggeredByButton = true
}
function filterByCountryFromSummary(countryCode) {
  filterCountry.value = countryCode || ''
  applyFilters()
}
function goToPage(pageNumber) {
  if (pageNumber >= 1 && pageNumber <= totalPages.value) {
    currentPage.value = pageNumber
    fetchData(false)
  }
}
function prevPage() {
  goToPage(currentPage.value - 1)
}
function nextPage() {
  goToPage(currentPage.value + 1)
}
function startAutoUpdate() {
  stopAutoUpdate()
  if (autoUpdateEnabled.value && autoUpdateInterval.value > 0) {
    if (!configError.value && apiClient) {
      autoUpdateIntervalId = setInterval(() => {
        console.log('Auto-Update: Fetching data...')
        fetchData(false)
      }, autoUpdateInterval.value * 1000)
      console.log(`Auto-Update started (Interval: ${autoUpdateInterval.value}s)`)
    } else {
      console.warn('Auto-Update not started: Configuration missing or invalid.')
    }
  }
}
function stopAutoUpdate() {
  if (autoUpdateIntervalId) {
    clearInterval(autoUpdateIntervalId)
    autoUpdateIntervalId = null
    console.log('Auto-Update stopped.')
  }
}
watch(autoUpdateEnabled, (newValue) => {
  if (newValue) {
    startAutoUpdate()
  } else {
    stopAutoUpdate()
  }
})
watch(autoUpdateInterval, () => {
  if (autoUpdateEnabled.value) {
    startAutoUpdate()
  }
})
function formatTimestamp(utcTimestamp) {
  if (!utcTimestamp) return 'N/A'
  try {
    let dateStr = utcTimestamp.endsWith('Z') ? utcTimestamp : utcTimestamp + 'Z'
    const date = new Date(dateStr)
    if (isNaN(date.getTime())) {
      throw new Error('Invalid date format.')
    }
    const options = {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
      timeZone: 'Europe/Berlin',
    }
    return date.toLocaleString('de-DE', options)
  } catch (e) {
    console.error('Error formatting timestamp:', utcTimestamp, e)
    return utcTimestamp
  }
}
function toggleDarkMode() {
  isDarkMode.value = !isDarkMode.value
  localStorage.setItem('darkMode', isDarkMode.value)
  updateHtmlClass()
}
function updateHtmlClass() {
  if (isDarkMode.value) {
    document.documentElement.classList.add('dark')
  } else {
    document.documentElement.classList.remove('dark')
  }
}

function formatCount(number) {
  if (typeof number !== 'number') {
    return number
  }
  try {
    return new Intl.NumberFormat('en-US', {
      notation: 'compact',
      compactDisplay: 'short',
    }).format(number)
  } catch (e) {
    console.error('Error formatting number:', number, e)
    return number
  }
}

// --- Lifecycle Hooks ---
onMounted(async () => {
  updateHtmlClass()
  const envApiUrl = import.meta.env.VITE_API_BASE_URL
  const envApiKey = import.meta.env.VITE_API_KEY
  if (envApiUrl && typeof envApiKey === 'string') {
    console.log('Configuration loaded from .env variables.')
    apiBaseUrl.value = envApiUrl
    apiKey.value = envApiKey
    configSource.value = 'env'
    configLoading.value = false
    apiClient = createApiClient()
    if (!apiClient) {
      configError.value = 'Error creating API client with .env configuration.'
    }
  } else {
    console.log('No .env variables found, trying to load /config.json...')
    await loadConfigFromJson()
  }
  if (!configError.value && apiClient) {
    if (autoUpdateEnabled.value) {
      startAutoUpdate()
    }
    fetchData(false)
  } else {
    error.value = configError.value || 'Configuration could not be loaded.'
    console.error('Initial data cannot be loaded:', error.value)
  }
})
onBeforeUnmount(() => {
  stopAutoUpdate()
  stopPolling()
  clearTimeout(filterButtonLoadingTimer)
})
</script>

<template>
  <div class="dashboard-container min-h-screen p-3 md:p-4 lg:p-6 bg-gray-100 dark:bg-gray-900">
    <header class="mb-4 flex justify-between items-center gap-4 flex-wrap">
      <div class="flex items-center gap-3 flex-shrink-0">
        <img src="/logo.png" alt="Logo" class="h-12 w-12" />
        <h1 class="text-xl sm:text-2xl font-mono text-gray-800 dark:text-gray-100">
          <span class="text-[#2d624a] font-bold text-2xl sm:text-3xl">S</span>erver
          <span class="text-[#2d624a] font-bold text-2xl sm:text-3xl">A</span>ccess
          <span class="text-[#2d624a] font-bold text-2xl sm:text-3xl">D</span>ashboard
        </h1>
      </div>
      <div class="flex items-center gap-3 flex-shrink-0">
        <span
          v-if="formattedLastUpdateTime"
          class="text-sm text-gray-500 dark:text-gray-400 hidden sm:inline"
        >
          Last update: {{ formattedLastUpdateTime }}
        </span>
        <button
          @click="collectAndRefreshLogs"
          :disabled="isCollectingAndRefreshing || configLoading || !!configError || !apiClient"
          title="Collect logs & refresh"
          class="p-2 rounded-full text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 dark:focus:ring-offset-gray-900 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            stroke-width="1.5"
            stroke="currentColor"
            class="w-6 h-6"
            :class="{ 'animate-spin': isCollectingAndRefreshing }"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
            />
          </svg>
        </button>
        <button
          @click="toggleDarkMode"
          title="Toggle Dark Mode"
          class="p-2 rounded-full text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 dark:focus:ring-offset-gray-900 transition-colors"
        >
          <svg
            v-if="isDarkMode"
            xmlns="http://www.w3.org/2000/svg"
            class="h-6 w-6"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            stroke-width="2"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
            />
          </svg>
          <svg
            v-else
            xmlns="http://www.w3.org/2000/svg"
            class="h-6 w-6"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            stroke-width="2"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
            />
          </svg>
        </button>
      </div>
    </header>

    <div
      v-if="configLoading"
      class="bg-yellow-100 border border-yellow-400 text-yellow-700 px-4 py-3 rounded relative mb-6"
      role="status"
    >
      <span class="block sm:inline"
        >Loading backend configuration... (Source: {{ configSource || 'Unknown' }})</span
      >
    </div>
    <div
      v-if="configError"
      class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative mb-6"
      role="alert"
    >
      <strong class="font-bold">Konfigurationsfehler!</strong>
      <span class="block sm:inline"> {{ configError }}</span>
    </div>
    <div
      v-if="error && !configError"
      class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative mb-6"
      role="alert"
    >
      <strong class="font-bold">API Fehler!</strong>
      <span class="block sm:inline"> {{ error }}</span>
    </div>
    <div
      v-if="isCollectingAndRefreshing"
      class="bg-purple-100 border border-purple-400 text-purple-700 px-4 py-3 rounded relative mb-6"
      role="status"
    >
      <span class="block sm:inline"
        >Logs are being collected and data is updating... (Polling Status)</span
      >
    </div>

    <div
      v-if="!configLoading && !configError && apiClient"
      class="main-content flex flex-col lg:flex-row lg:items-start gap-6"
    >
      <aside class="left-sidebar w-full lg:w-80 xl:w-96 flex-shrink-0 space-y-6">
        <div class="controls-container bg-white dark:bg-gray-800 shadow-lg rounded p-4">
          <h3
            class="text-lg font-semibold text-gray-700 dark:text-gray-300 mb-4 border-b border-gray-200 dark:border-gray-700 pb-2"
          >
            Filter
          </h3>
          <div class="space-y-4">
            <div>
              <label
                for="filterIp"
                class="block mb-1 text-sm font-medium text-gray-600 dark:text-gray-400"
                >IP contains:</label
              ><input
                id="filterIp"
                type="text"
                v-model="filterIp"
                placeholder="e.g. 192.168..."
                class="w-full box-border min-h-[42px] rounded border border-gray-300 bg-white p-2 text-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200"
              />
            </div>
            <div>
              <label
                for="filterCountry"
                class="block mb-1 text-sm font-medium text-gray-600 dark:text-gray-400"
                >Country contains:</label
              ><input
                id="filterCountry"
                type="text"
                v-model="filterCountry"
                placeholder="e.g. DE or Unknown"
                class="w-full box-border min-h-[42px] rounded border border-gray-300 bg-white p-2 text-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200"
              />
            </div>
            <div>
              <label
                for="filterDomain"
                class="block mb-1 text-sm font-medium text-gray-600 dark:text-gray-400"
                >Domain contains:</label
              ><input
                id="filterDomain"
                type="text"
                v-model="filterDomain"
                placeholder="e.g. example.com"
                class="w-full box-border min-h-[42px] rounded border border-gray-300 bg-white p-2 text-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200"
              />
            </div>
            <div>
              <label
                for="filterStatus"
                class="block mb-1 text-sm font-medium text-gray-600 dark:text-gray-400"
                >Status Code:</label
              ><select
                id="filterStatus"
                v-model="filterStatusCode"
                class="w-full box-border min-h-[42px] rounded border border-gray-300 bg-white p-2 text-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200"
              >
                <option :value="null">All</option>
                <option value="200">200 OK</option>
                <option value="201">201 Created</option>
                <option value="202">202 Accepted</option>
                <option value="204">204 No Content</option>
                <option value="301">301 Moved</option>
                <option value="302">302 Found</option>
                <option value="304">304 Not Modified</option>
                <option value="400">400 Bad Request</option>
                <option value="401">401 Unauthorized</option>
                <option value="403">403 Forbidden</option>
                <option value="404">404 Not Found</option>
                <option value="499">499 Client Closed</option>
                <option value="500">500 Server Error</option>
                <option value="502">502 Bad Gateway</option>
                <option value="503">503 Unavailable</option>
              </select>
            </div>
            <button
              @click="applyFilters"
              :class="{
                'opacity-50 cursor-not-allowed':
                  showDelayedFilterButtonLoading || isCollectingAndRefreshing,
                'hover:bg-blue-700': !showDelayedFilterButtonLoading && !isCollectingAndRefreshing,
              }"
              class="filter-button w-full box-border min-h-[42px] rounded bg-blue-600 px-4 py-2 font-semibold text-white shadow transition-opacity duration-150 relative overflow-hidden"
            >
              {{ showDelayedFilterButtonLoading ? 'Filtering...' : 'Apply Filter' }}
              <span
                v-if="rippleActive"
                class="ripple"
                :style="{ left: `${rippleX}px`, top: `${rippleY}px` }"
              ></span>
            </button>
          </div>
          <div
            class="auto-update mt-4 flex justify-end border-t border-gray-200 pt-4 dark:border-gray-700"
          >
            <label
              class="flex cursor-pointer items-center gap-2 text-sm text-gray-600 dark:text-gray-400"
              ><input
                type="checkbox"
                v-model="autoUpdateEnabled"
                class="h-5 w-5 rounded border-gray-300 bg-white text-blue-600 focus:ring-blue-500 dark:border-gray-500 dark:bg-gray-600"
              />
              Auto-Update ({{ autoUpdateInterval }}s)</label
            >
          </div>
        </div>

        <section class="summary bg-white dark:bg-gray-800 shadow-md rounded p-5">
          <h2
            class="pb-2 mb-4 text-lg font-semibold text-gray-700 border-b border-gray-200 dark:text-gray-200 dark:border-gray-700"
          >
            Accesses by Country
          </h2>
          <ul v-if="summary.length > 0" class="space-y-2 max-h-[40vh] overflow-y-auto pr-2">
            <li
              v-for="item in summary"
              :key="item.country || 'unknown'"
              class="flex items-center justify-between pb-1 text-sm border-b border-gray-100 dark:border-gray-700 last:border-b-0"
              @click="filterByCountryFromSummary(item.country)"
              :class="{ 'cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50': true }"
              :title="item.country ? `Filter by ${item.country}` : 'Filter by Unknown'"
            >
              <span class="flex items-center gap-2">
                <country-flag
                  v-if="item.country && item.country !== 'Unknown'"
                  :country="item.country"
                  size="small"
                />
                <span v-else class="text-gray-400 dark:text-gray-500 inline-block w-4 text-center">
                  ❔
                </span>
                <span v-if="item.country" class="font-medium text-gray-700 dark:text-gray-300 ml-1">
                  {{ item.country }}
                </span>
              </span>
              <span class="font-medium text-gray-600 dark:text-gray-400">{{
                formatCount(item.count)
              }}</span>
            </li>
          </ul>
          <p
            v-else-if="!loading && !isCollectingAndRefreshing"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            No summary data available.
          </p>
          <p v-else class="text-sm text-gray-500 dark:text-gray-400">Loading summary...</p>
        </section>
      </aside>

      <section class="logs flex-1 bg-white dark:bg-gray-800 shadow-md rounded p-5 min-w-0">
        <div
          class="pb-2 mb-4 flex flex-wrap items-center justify-between gap-4 border-b border-gray-200 dark:border-gray-700"
        >
          <h2 class="text-lg font-semibold text-gray-700 dark:text-gray-200 flex-shrink-0 mr-auto">
            Log-Entries<span
              v-if="(loading || isCollectingAndRefreshing) && autoUpdateEnabled"
              class="ml-2 text-sm font-normal text-gray-500"
              >(updating...)</span
            >
          </h2>
          <span class="text-sm font-normal text-gray-500 flex-shrink-0" v-if="totalItems > 0"
            >({{ formatCount(totalItems) }} total)</span
          >
          <div
            v-if="totalPages > 1"
            class="pagination-controls flex items-center justify-center gap-2 flex-shrink-0"
          >
            <button
              @click="prevPage"
              :disabled="currentPage === 1 || loading || isCollectingAndRefreshing"
              class="rounded bg-gray-200 px-2 py-1 text-xs text-gray-800 transition-colors hover:bg-gray-300 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-600 dark:text-gray-200 dark:hover:bg-gray-500"
            >
              &lt;</button
            ><span class="text-xs text-gray-700 dark:text-gray-300">
              Page {{ currentPage }}/{{ totalPages }} </span
            ><button
              @click="nextPage"
              :disabled="currentPage === totalPages || loading || isCollectingAndRefreshing"
              class="rounded bg-gray-200 px-2 py-1 text-xs text-gray-800 transition-colors hover:bg-gray-300 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-600 dark:text-gray-200 dark:hover:bg-gray-500"
            >
              &gt;
            </button>
          </div>
        </div>
        <div
          v-if="logs.length > 0"
          class="overflow-x-auto border border-gray-200 rounded dark:border-gray-700"
        >
          <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead class="bg-gray-50 dark:bg-gray-700/50">
              <tr>
                <th
                  scope="col"
                  @click="sortBy('timestamp')"
                  class="sticky top-0 z-10 cursor-pointer select-none whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-600"
                >
                  Timestamp<span v-if="sortKey === 'timestamp'" class="ml-1 text-blue-500">{{
                    sortDirection === 'asc' ? '▲' : '▼'
                  }}</span>
                </th>
                <th
                  scope="col"
                  @click="sortBy('ip_address')"
                  class="sticky top-0 z-10 cursor-pointer select-none whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-600"
                >
                  IP Adress<span v-if="sortKey === 'ip_address'" class="ml-1 text-blue-500">{{
                    sortDirection === 'asc' ? '▲' : '▼'
                  }}</span>
                </th>
                <th
                  scope="col"
                  @click="sortBy('status_code')"
                  class="sticky top-0 z-10 cursor-pointer select-none whitespace-nowrap px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider text-gray-500 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-600"
                >
                  Status Code<span v-if="sortKey === 'status_code'" class="ml-1 text-blue-500">{{
                    sortDirection === 'asc' ? '▲' : '▼'
                  }}</span>
                </th>
                <th
                  scope="col"
                  @click="sortBy('country')"
                  class="sticky top-0 z-10 cursor-pointer select-none whitespace-nowrap px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider text-gray-500 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-600"
                >
                  Country<span v-if="sortKey === 'country'" class="ml-1 text-blue-500">{{
                    sortDirection === 'asc' ? '▲' : '▼'
                  }}</span>
                </th>
                <th
                  scope="col"
                  @click="sortBy('domain')"
                  class="sticky top-0 z-10 cursor-pointer select-none whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-600"
                >
                  Domain<span v-if="sortKey === 'domain'" class="ml-1 text-blue-500">{{
                    sortDirection === 'asc' ? '▲' : '▼'
                  }}</span>
                </th>
                <th
                  scope="col"
                  @click="sortBy('request_path')"
                  class="sticky top-0 z-10 cursor-pointer select-none whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-600"
                >
                  Path<span v-if="sortKey === 'request_path'" class="ml-1 text-blue-500">{{
                    sortDirection === 'asc' ? '▲' : '▼'
                  }}</span>
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-800">
              <tr
                v-for="log in logs"
                :key="log.id"
                class="transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/50"
              >
                <td class="whitespace-nowrap px-4 py-3 text-sm text-gray-700 dark:text-gray-300">
                  {{ formatTimestamp(log.timestamp) }}
                </td>
                <td
                  class="whitespace-nowrap px-4 py-3 font-mono text-xs text-gray-700 dark:text-gray-300"
                >
                  {{ log.ip_address }}
                </td>
                <td class="whitespace-nowrap px-4 py-3 text-center text-sm">
                  <span :class="['status-badge', `status-${log.status_code}`]">{{
                    log.status_code !== null ? log.status_code : 'N/A'
                  }}</span>
                </td>
                <td class="whitespace-nowrap px-4 py-3 text-center text-sm">
                  <country-flag
                    v-if="log.country && log.country !== 'Unknown'"
                    :country="log.country"
                    size="normal"
                  />
                  <span v-else class="text-gray-400 dark:text-gray-500">❔</span>
                </td>
                <td class="whitespace-nowrap px-4 py-3 text-sm text-gray-700 dark:text-gray-300">
                  {{ log.domain || 'N/A' }}
                </td>
                <td
                  class="max-w-xs sm:max-w-sm md:max-w-md lg:max-w-lg xl:max-w-xl overflow-hidden text-ellipsis whitespace-nowrap px-4 py-3 text-sm text-gray-700 dark:text-gray-300"
                  :title="log.request_path"
                >
                  {{ log.request_path || 'N/A' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p
          v-else-if="!loading && !isCollectingAndRefreshing"
          class="mt-4 text-sm text-gray-500 dark:text-gray-400"
        >
          No log data available (or filter returned no results).
        </p>
        <p v-else class="mt-4 text-sm text-gray-500 dark:text-gray-400">Loading Logs...</p>
      </section>
    </div>
    <div v-else-if="!configLoading" class="text-center mt-10 text-gray-500 dark:text-gray-400">
      <p>
        The application cannot be loaded because the backend configuration is missing or invalid.
      </p>
      <p>
        Source: {{ configSource || 'Unknown' }}. Error message:
        {{ configError || 'No details' }}
      </p>
      <p>Please check the browser console and the Docker container logs for more details.</p>
    </div>
  </div>
</template>

<style scoped>
/* Basic styling for the button (relative position for the ripple) */
.filter-button {
  position: relative;
  overflow: hidden;
}

/* Styling for the ripple effect */
.ripple {
  position: absolute;
  border-radius: 50%;
  background-color: rgba(255, 255, 255, 0.7);
  width: 100px;
  height: 100px;
  margin-top: -50px;
  margin-left: -50px;
  animation: ripple-animation 0.6s linear;
  transform: scale(0);
  opacity: 1;
  pointer-events: none;
}

/* Keyframes for the animation */
@keyframes ripple-animation {
  to {
    transform: scale(4);
    opacity: 0;
  }
}

.status-badge {
  padding: 3px 8px;
  border-radius: 12px;
  font-size: 0.75em;
  color: white;
  min-width: 30px;
  display: inline-block;
  text-align: center;
  font-weight: bold;
  line-height: 1.2;
  vertical-align: middle;
}
.status-200,
.status-201,
.status-202,
.status-204 {
  background-color: #28a745;
}
.status-301,
.status-302,
.status-304 {
  background-color: #ffc107;
  color: #333;
}
.status-400,
.status-401,
.status-403,
.status-404,
.status-499 {
  background-color: #dc3545;
}
.status-500,
.status-502,
.status-503 {
  background-color: #6c757d;
}
.status-badge:not([class*='status-2']):not([class*='status-3']):not([class*='status-4']):not(
    [class*='status-5']
  ) {
  background-color: #adb5bd;
  color: #333;
}
table thead th {
  background-color: inherit;
  z-index: 10;
}
.overflow-x-auto {
  -webkit-overflow-scrolling: touch;
}
.dark .dashboard-container {
  background-color: #111827;
}
.dark .controls-container,
.dark .summary,
.dark .logs {
  background-color: #1f2937;
}
.dark table thead {
  background-color: #374151;
}
.dark tbody {
  background-color: #1f2937;
  border-color: #374151;
}
.dark tr:hover {
  background-color: #374151;
}
.animate-spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
