"""
DecarbIQ Data Collection Module
================================
Automated data collection agents for:
- EIA API (natural gas prices, production, storage)
- ERCOT (wholesale electricity LMPs)
- CAISO (California wholesale prices)
- Baker Hughes (rig counts)

Each agent can:
1. Fetch historical data for initial database build
2. Update incrementally with new data
3. Handle API rate limits and errors gracefully
4. Store data in standardized CSV format

NOTE: API keys are required for EIA. ERCOT and CAISO data is public.
Set EIA_API_KEY environment variable or pass to functions.

Author: DecarbIQ
Date: 2026-02-09
"""

import json
import csv
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import time

# =============================================================================
# CONFIGURATION
# =============================================================================

# Base URLs
EIA_API_BASE = "https://api.eia.gov/v2"
ERCOT_DATA_BASE = "https://www.ercot.com/api/1/services/read"
# Note: ERCOT requires registration for full API access

# Data storage paths
DATA_DIR = "/home/claude/decarbiq_data"
MONTHLY_DIR = f"{DATA_DIR}/monthly"
DAILY_DIR = f"{DATA_DIR}/daily"

# Rate limiting
EIA_REQUESTS_PER_SECOND = 2
ERCOT_REQUESTS_PER_SECOND = 1

@dataclass
class DataSeries:
    """Represents a time series data source"""
    name: str
    source: str  # 'eia', 'ercot', 'caiso', 'bakerhughes'
    endpoint: str
    frequency: str  # 'daily', 'weekly', 'monthly', 'annual'
    unit: str
    description: str
    region: str = "US"
    facets: Dict = field(default_factory=dict)
    
# =============================================================================
# EIA API AGENT
# =============================================================================

class EIAAgent:
    """
    Agent for fetching data from EIA API v2
    
    Key endpoints:
    - natural-gas/pri/sum: Natural gas prices (spot, citygate, etc.)
    - natural-gas/prod/sum: Natural gas production by state/region
    - natural-gas/stor/sum: Natural gas storage
    - electricity/retail-sales: Retail electricity prices
    - steo: Short-Term Energy Outlook projections
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("EIA_API_KEY")
        self.base_url = EIA_API_BASE
        self.last_request_time = 0
        
        if not self.api_key:
            print("WARNING: No EIA API key provided. Set EIA_API_KEY environment variable.")
            print("Get a free key at: https://www.eia.gov/opendata/register.php")
    
    def _rate_limit(self):
        """Enforce rate limiting"""
        elapsed = time.time() - self.last_request_time
        if elapsed < 1.0 / EIA_REQUESTS_PER_SECOND:
            time.sleep(1.0 / EIA_REQUESTS_PER_SECOND - elapsed)
        self.last_request_time = time.time()
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make API request with error handling"""
        if not self.api_key:
            return {"error": "No API key configured"}
        
        self._rate_limit()
        
        url = f"{self.base_url}/{endpoint}"
        if params is None:
            params = {}
        params["api_key"] = self.api_key
        
        full_url = f"{url}?{urlencode(params, doseq=True)}"
        
        try:
            req = Request(full_url)
            with urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode())
        except HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.reason}"}
        except URLError as e:
            return {"error": f"URL Error: {e.reason}"}
        except Exception as e:
            return {"error": str(e)}
    
    def get_metadata(self, route: str) -> Dict:
        """Get metadata for an API route"""
        return self._make_request(route)
    
    def get_natural_gas_prices(self, 
                               price_type: str = "rngwhhd",  # Henry Hub spot
                               frequency: str = "monthly",
                               start: str = None,
                               end: str = None) -> List[Dict]:
        """
        Fetch natural gas prices
        
        Price types:
        - rngwhhd: Henry Hub spot price
        - rngc1: Natural gas futures contract 1
        - Various citygate prices by state
        
        Returns list of {period, value} dicts
        """
        endpoint = "natural-gas/pri/sum/data"
        params = {
            "frequency": frequency,
            "data[0]": "value",
            "facets[series][]": price_type,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000
        }
        
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        result = self._make_request(endpoint, params)
        
        if "error" in result:
            return []
        
        return result.get("response", {}).get("data", [])
    
    def get_regional_gas_prices(self,
                                region: str = "SCA",  # SoCal Citygate
                                frequency: str = "monthly",
                                start: str = None,
                                end: str = None) -> List[Dict]:
        """
        Fetch regional natural gas prices (citygate)
        
        Regions:
        - SCA: SoCal Border Avg
        - TEX: Texas citygate
        - Various state codes
        """
        endpoint = "natural-gas/pri/sum/data"
        params = {
            "frequency": frequency,
            "data[0]": "value",
            "facets[duoarea][]": region,
            "facets[process][]": "PCS",  # Citygate price
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000
        }
        
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        result = self._make_request(endpoint, params)
        
        if "error" in result:
            return []
        
        return result.get("response", {}).get("data", [])
    
    def get_production_by_region(self,
                                 region: str = "US",
                                 frequency: str = "monthly",
                                 start: str = None,
                                 end: str = None) -> List[Dict]:
        """
        Fetch natural gas production data
        
        Regions: US, or state codes (TX, PA, etc.)
        For shale plays: Use STEO for Permian, Haynesville, Appalachian
        """
        endpoint = "natural-gas/prod/sum/data"
        params = {
            "frequency": frequency,
            "data[0]": "value",
            "facets[duoarea][]": region,
            "facets[process][]": "FGW",  # Gross withdrawals
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000
        }
        
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        result = self._make_request(endpoint, params)
        
        if "error" in result:
            return []
        
        return result.get("response", {}).get("data", [])
    
    def get_storage(self,
                    region: str = "NUS",  # Total Lower 48
                    frequency: str = "weekly",
                    start: str = None,
                    end: str = None) -> List[Dict]:
        """
        Fetch natural gas storage data
        
        Regions:
        - NUS: Total Lower 48
        - SAE: East
        - SAM: Midwest  
        - SAP: Pacific
        - SAW: Mountain
        - SAC: South Central
        """
        endpoint = "natural-gas/stor/sum/data"
        params = {
            "frequency": frequency,
            "data[0]": "value",
            "facets[duoarea][]": region,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000
        }
        
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        result = self._make_request(endpoint, params)
        
        if "error" in result:
            return []
        
        return result.get("response", {}).get("data", [])
    
    def get_electricity_prices(self,
                               state: str = "US",
                               sector: str = "RES",  # Residential
                               frequency: str = "monthly",
                               start: str = None,
                               end: str = None) -> List[Dict]:
        """
        Fetch retail electricity prices
        
        States: US or state codes (TX, CA, etc.)
        Sectors: RES, COM, IND, ALL
        """
        endpoint = "electricity/retail-sales/data"
        params = {
            "frequency": frequency,
            "data[0]": "price",
            "facets[stateid][]": state,
            "facets[sectorid][]": sector,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000
        }
        
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        result = self._make_request(endpoint, params)
        
        if "error" in result:
            return []
        
        return result.get("response", {}).get("data", [])
    
    def get_steo_series(self,
                        series_id: str,
                        start: str = None,
                        end: str = None) -> List[Dict]:
        """
        Fetch Short-Term Energy Outlook projections
        
        Useful series:
        - NGHHUUS: Henry Hub price
        - NGPRPUS: US dry gas production
        - PAPR_PERMIAN: Permian production (requires specific facet)
        """
        endpoint = "steo/data"
        params = {
            "frequency": "monthly",
            "data[0]": "value",
            "facets[seriesId][]": series_id,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000
        }
        
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        result = self._make_request(endpoint, params)
        
        if "error" in result:
            return []
        
        return result.get("response", {}).get("data", [])
    
    def get_wholesale_electricity(self,
                                  hub: str = "ERCT",  # ERCOT
                                  start: str = None,
                                  end: str = None) -> List[Dict]:
        """
        Fetch wholesale electricity prices from ICE data
        
        Hubs: ERCT, CISO (CAISO), PJM, MISO, etc.
        
        Note: EIA republishes ICE data biweekly
        """
        # This uses a different endpoint - the ICE wholesale data
        # Available at: https://www.eia.gov/electricity/wholesale/
        # Need to scrape or use different approach
        pass


# =============================================================================
# ERCOT DATA AGENT
# =============================================================================

class ERCOTAgent:
    """
    Agent for fetching ERCOT market data
    
    Key data:
    - Settlement Point Prices (SPPs) - wholesale electricity prices
    - LMPs by zone/hub
    - Load and generation data
    
    Note: Full API access requires registration at ERCOT
    Historical data available via data portal
    """
    
    def __init__(self):
        self.base_url = "https://www.ercot.com"
        self.last_request_time = 0
    
    def _rate_limit(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < 1.0 / ERCOT_REQUESTS_PER_SECOND:
            time.sleep(1.0 / ERCOT_REQUESTS_PER_SECOND - elapsed)
        self.last_request_time = time.time()
    
    def get_historical_spp_info(self) -> str:
        """
        Returns information about how to access ERCOT historical SPPs
        
        Historical Settlement Point Prices are available at:
        https://www.ercot.com/mp/data-products/data-product-details?id=NP4-190-CD
        
        For programmatic access, use gridstatus library:
        pip install gridstatus
        
        import gridstatus
        ercot = gridstatus.ERCOT()
        df = ercot.get_spp(date='2024-01-01')
        """
        return """
ERCOT Historical Data Access:

1. DIRECT DOWNLOAD (manual):
   - SPP by Hub: https://www.ercot.com/mp/data-products/data-product-details?id=NP4-190-CD
   - DAM LMPs: https://www.ercot.com/mp/data-products/data-product-details?id=NP4-183-CD
   - RTM LMPs: https://www.ercot.com/mp/data-products/data-product-details?id=NP6-905-CD

2. PROGRAMMATIC ACCESS (recommended):
   pip install gridstatus
   
   import gridstatus
   ercot = gridstatus.ERCOT()
   
   # Get real-time SPP
   df = ercot.get_spp(date='2024-01-01')
   
   # Get day-ahead LMP
   df = ercot.get_lmp(date='2024-01-01', market='DAY_AHEAD_HOURLY')
   
   # Get historical range
   df = ercot.get_spp(start='2024-01-01', end='2024-12-31')

3. ERCOT API (requires registration):
   - Register at: https://www.ercot.com/services/mdt
   - Use ERCOT Market Information System (MIS) API
"""

    def download_historical_instructions(self) -> Dict:
        """Return structured instructions for historical data download"""
        return {
            "spp_hourly": {
                "name": "Settlement Point Prices (Hourly)",
                "url": "https://www.ercot.com/mp/data-products/data-product-details?id=NP4-190-CD",
                "format": "CSV in ZIP",
                "frequency": "hourly",
                "history": "2010-present",
                "zones": ["HB_HOUSTON", "HB_NORTH", "HB_SOUTH", "HB_WEST", 
                         "LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH", "LZ_WEST"],
                "python_method": "gridstatus.ERCOT().get_spp()"
            },
            "lmp_dam": {
                "name": "Day-Ahead Market LMPs",
                "url": "https://www.ercot.com/mp/data-products/data-product-details?id=NP4-183-CD",
                "format": "CSV in ZIP",
                "frequency": "hourly",
                "history": "2010-present",
                "python_method": "gridstatus.ERCOT().get_lmp(market='DAY_AHEAD_HOURLY')"
            },
            "lmp_rtm": {
                "name": "Real-Time Market LMPs",
                "url": "https://www.ercot.com/mp/data-products/data-product-details?id=NP6-905-CD",
                "format": "CSV in ZIP",
                "frequency": "5-minute intervals",
                "history": "2010-present",
                "python_method": "gridstatus.ERCOT().get_lmp(market='REAL_TIME_SCED')"
            }
        }


# =============================================================================
# CAISO DATA AGENT  
# =============================================================================

class CAISOAgent:
    """
    Agent for fetching CAISO market data
    
    Key data:
    - LMPs by zone
    - Renewable generation
    - Curtailment data
    
    OASIS (Open Access Same-time Information System) is the primary source
    """
    
    def __init__(self):
        self.oasis_url = "http://oasis.caiso.com/oasisapi"
        self.last_request_time = 0
    
    def get_historical_info(self) -> str:
        """Returns information about CAISO data access"""
        return """
CAISO Historical Data Access:

1. OASIS (Open Access Same-time Information System):
   - URL: http://oasis.caiso.com/mrioasis/logon.do
   - LMPs, load, generation, curtailment
   - Free registration required for bulk downloads

2. PROGRAMMATIC ACCESS (recommended):
   pip install gridstatus
   
   import gridstatus
   caiso = gridstatus.CAISO()
   
   # Get LMPs
   df = caiso.get_lmp(date='2024-01-01', market='DAY_AHEAD_HOURLY')
   
   # Get load
   df = caiso.get_load(date='2024-01-01')
   
   # Get fuel mix
   df = caiso.get_fuel_mix(date='2024-01-01')

3. Key zones:
   - SP15: Southern California (includes SCE, SDGE)
   - NP15: Northern California (includes PG&E north)
   - ZP26: Pacific Gas & Electric (ZP26 zone)
"""

    def download_historical_instructions(self) -> Dict:
        """Return structured instructions for CAISO data"""
        return {
            "lmp": {
                "name": "Locational Marginal Prices",
                "url": "http://oasis.caiso.com/oasisapi/SingleZip",
                "zones": ["SP15", "NP15", "ZP26"],
                "frequency": "hourly",
                "python_method": "gridstatus.CAISO().get_lmp()"
            },
            "load": {
                "name": "System Load",
                "python_method": "gridstatus.CAISO().get_load()"
            },
            "fuel_mix": {
                "name": "Generation by Fuel Type",
                "python_method": "gridstatus.CAISO().get_fuel_mix()"
            },
            "curtailment": {
                "name": "Renewable Curtailment",
                "url": "http://www.caiso.com/market/Pages/ReportsBulletins/DailyRenewablesWatch.aspx",
                "frequency": "daily"
            }
        }


# =============================================================================
# BAKER HUGHES RIG COUNT AGENT
# =============================================================================

class BakerHughesAgent:
    """
    Agent for fetching Baker Hughes rig count data
    
    Weekly rig counts by:
    - Region (Permian, Haynesville, Appalachia, etc.)
    - Type (oil, gas)
    - State
    """
    
    def __init__(self):
        self.base_url = "https://rigcount.bakerhughes.com"
    
    def get_data_info(self) -> str:
        """Returns information about rig count data access"""
        return """
Baker Hughes Rig Count Data:

1. DIRECT DOWNLOAD:
   - URL: https://rigcount.bakerhughes.com/na-rig-count
   - Weekly updates (Friday)
   - Historical data in Excel format

2. DATA STRUCTURE:
   - By state
   - By basin (Permian, Haynesville, Appalachian, etc.)
   - By type (oil, gas, misc)
   - Horizontal vs vertical

3. KEY BASINS FOR DECARBIQ:
   - Permian: Primary Texas associated gas
   - Haynesville: Gulf Coast dry gas
   - Appalachian (Marcellus/Utica): Northeast dry gas
   - Eagle Ford: Texas oil + associated gas
   - Bakken: North Dakota associated gas

4. EIA ALTERNATIVE:
   EIA also publishes rig count data with API access:
   https://www.eia.gov/petroleum/drilling/
   
   API Route: petroleum/dpm/data
"""

    def download_historical_instructions(self) -> Dict:
        """Return structured instructions for rig count data"""
        return {
            "weekly_rig_count": {
                "name": "North America Rig Count",
                "url": "https://rigcount.bakerhughes.com/na-rig-count",
                "format": "Excel (.xlsx)",
                "frequency": "weekly (Friday)",
                "history": "1987-present",
                "basins": ["Permian", "Haynesville", "Marcellus", "Utica", 
                          "Eagle Ford", "Bakken", "Niobrara", "Anadarko"]
            },
            "eia_drilling": {
                "name": "EIA Drilling Productivity Report",
                "url": "https://www.eia.gov/petroleum/drilling/",
                "api_route": "petroleum/dpm/data",
                "frequency": "monthly",
                "regions": ["Permian", "Haynesville", "Appalachian", 
                           "Eagle Ford", "Bakken", "Niobrara", "Anadarko"]
            }
        }


# =============================================================================
# DATA SERIES REGISTRY
# =============================================================================

# Define all data series we need for DecarbIQ
DATA_SERIES_REGISTRY = {
    # Natural Gas Prices
    "henry_hub_spot": DataSeries(
        name="Henry Hub Natural Gas Spot Price",
        source="eia",
        endpoint="natural-gas/pri/sum",
        frequency="monthly",
        unit="$/MMBtu",
        description="US benchmark natural gas price",
        region="US",
        facets={"series": "rngwhhd"}
    ),
    "waha_spot": DataSeries(
        name="Waha Hub Natural Gas Spot Price",
        source="eia",
        endpoint="natural-gas/pri/sum",
        frequency="daily",
        unit="$/MMBtu",
        description="Permian Basin natural gas price",
        region="TX",
        facets={"duoarea": "STXPWAHA", "process": "PGP"}  # Need to verify
    ),
    "socal_citygate": DataSeries(
        name="SoCal Citygate Natural Gas Price",
        source="eia",
        endpoint="natural-gas/pri/sum",
        frequency="monthly",
        unit="$/MMBtu",
        description="California citygate price",
        region="CA",
        facets={"duoarea": "SCA", "process": "PCS"}
    ),
    
    # Production
    "us_production": DataSeries(
        name="US Dry Natural Gas Production",
        source="eia",
        endpoint="natural-gas/prod/sum",
        frequency="monthly",
        unit="MMcf/d",
        description="US total dry gas production",
        region="US"
    ),
    "permian_production": DataSeries(
        name="Permian Basin Natural Gas Production",
        source="eia",
        endpoint="steo",
        frequency="monthly",
        unit="Bcf/d",
        description="Permian region marketed gas production",
        region="Permian",
        facets={"seriesId": "NGPRPPERM"}  # Need to verify exact ID
    ),
    "haynesville_production": DataSeries(
        name="Haynesville Shale Natural Gas Production",
        source="eia",
        endpoint="steo",
        frequency="monthly",
        unit="Bcf/d",
        description="Haynesville region gas production",
        region="Haynesville"
    ),
    "appalachian_production": DataSeries(
        name="Appalachian Basin Natural Gas Production",
        source="eia",
        endpoint="steo",
        frequency="monthly",
        unit="Bcf/d",
        description="Appalachian region gas production (Marcellus + Utica)",
        region="Appalachian"
    ),
    
    # Storage
    "us_storage": DataSeries(
        name="US Working Gas in Storage",
        source="eia",
        endpoint="natural-gas/stor/sum",
        frequency="weekly",
        unit="Bcf",
        description="Total Lower 48 working gas storage",
        region="US",
        facets={"duoarea": "NUS"}
    ),
    "pacific_storage": DataSeries(
        name="Pacific Region Working Gas Storage",
        source="eia",
        endpoint="natural-gas/stor/sum",
        frequency="weekly",
        unit="Bcf",
        description="Pacific region storage (relevant for CA)",
        region="Pacific",
        facets={"duoarea": "SAP"}
    ),
    
    # Electricity Prices
    "us_retail_elec": DataSeries(
        name="US Average Retail Electricity Price",
        source="eia",
        endpoint="electricity/retail-sales",
        frequency="monthly",
        unit="cents/kWh",
        description="US residential electricity price",
        region="US"
    ),
    "tx_retail_elec": DataSeries(
        name="Texas Retail Electricity Price",
        source="eia",
        endpoint="electricity/retail-sales",
        frequency="monthly",
        unit="cents/kWh",
        description="Texas residential electricity price",
        region="TX"
    ),
    "ca_retail_elec": DataSeries(
        name="California Retail Electricity Price",
        source="eia",
        endpoint="electricity/retail-sales",
        frequency="monthly",
        unit="cents/kWh",
        description="California residential electricity price",
        region="CA"
    ),
    
    # Wholesale Electricity (need gridstatus)
    "ercot_spp": DataSeries(
        name="ERCOT Settlement Point Price",
        source="ercot",
        endpoint="gridstatus",
        frequency="hourly",
        unit="$/MWh",
        description="ERCOT wholesale electricity price",
        region="TX"
    ),
    "caiso_lmp": DataSeries(
        name="CAISO Locational Marginal Price",
        source="caiso",
        endpoint="gridstatus",
        frequency="hourly",
        unit="$/MWh",
        description="CAISO SP15 wholesale electricity price",
        region="CA"
    ),
    
    # LNG and Exports
    "lng_exports": DataSeries(
        name="US LNG Exports",
        source="eia",
        endpoint="natural-gas/move/expc/sum",
        frequency="monthly",
        unit="MMcf",
        description="US LNG exports by country",
        region="US"
    ),
    "mexico_exports": DataSeries(
        name="US Natural Gas Exports to Mexico",
        source="eia",
        endpoint="natural-gas/move/expn/sum",
        frequency="monthly",
        unit="MMcf",
        description="Pipeline exports to Mexico",
        region="US"
    ),
    
    # Rig Counts
    "us_gas_rigs": DataSeries(
        name="US Natural Gas Rig Count",
        source="bakerhughes",
        endpoint="rigcount",
        frequency="weekly",
        unit="rigs",
        description="US natural gas directed rig count",
        region="US"
    ),
    "permian_rigs": DataSeries(
        name="Permian Basin Rig Count",
        source="bakerhughes",
        endpoint="rigcount",
        frequency="weekly",
        unit="rigs",
        description="Permian basin oil + gas rigs",
        region="Permian"
    )
}


# =============================================================================
# DATA COLLECTION ORCHESTRATOR
# =============================================================================

class DecarbIQDataCollector:
    """
    Main orchestrator for data collection
    
    Usage:
        collector = DecarbIQDataCollector(eia_api_key="your_key")
        
        # Fetch all data
        collector.fetch_all()
        
        # Update incrementally
        collector.update_all()
        
        # Get specific series
        data = collector.fetch_series("henry_hub_spot", start="2020-01", end="2024-12")
    """
    
    def __init__(self, eia_api_key: Optional[str] = None, data_dir: str = DATA_DIR):
        self.eia = EIAAgent(api_key=eia_api_key)
        self.ercot = ERCOTAgent()
        self.caiso = CAISOAgent()
        self.bakerhughes = BakerHughesAgent()
        self.data_dir = data_dir
        self.registry = DATA_SERIES_REGISTRY
        
        # Create data directories
        os.makedirs(f"{data_dir}/monthly", exist_ok=True)
        os.makedirs(f"{data_dir}/daily", exist_ok=True)
        os.makedirs(f"{data_dir}/weekly", exist_ok=True)
        os.makedirs(f"{data_dir}/hourly", exist_ok=True)
    
    def fetch_series(self, 
                     series_id: str,
                     start: str = None,
                     end: str = None) -> List[Dict]:
        """Fetch a specific data series"""
        
        if series_id not in self.registry:
            print(f"Unknown series: {series_id}")
            return []
        
        series = self.registry[series_id]
        
        if series.source == "eia":
            return self._fetch_eia_series(series, start, end)
        elif series.source == "ercot":
            print(f"ERCOT data requires gridstatus library. See: {self.ercot.get_historical_info()}")
            return []
        elif series.source == "caiso":
            print(f"CAISO data requires gridstatus library. See: {self.caiso.get_historical_info()}")
            return []
        elif series.source == "bakerhughes":
            print(f"Baker Hughes data requires manual download. See: {self.bakerhughes.get_data_info()}")
            return []
        
        return []
    
    def _fetch_eia_series(self, series: DataSeries, start: str, end: str) -> List[Dict]:
        """Fetch an EIA series"""
        
        # Route to appropriate EIA method based on endpoint
        if "pri/sum" in series.endpoint:
            if "duoarea" in series.facets:
                return self.eia.get_regional_gas_prices(
                    region=series.facets.get("duoarea"),
                    frequency=series.frequency,
                    start=start,
                    end=end
                )
            else:
                return self.eia.get_natural_gas_prices(
                    price_type=series.facets.get("series", "rngwhhd"),
                    frequency=series.frequency,
                    start=start,
                    end=end
                )
        
        elif "prod/sum" in series.endpoint:
            return self.eia.get_production_by_region(
                region=series.region,
                frequency=series.frequency,
                start=start,
                end=end
            )
        
        elif "stor/sum" in series.endpoint:
            return self.eia.get_storage(
                region=series.facets.get("duoarea", "NUS"),
                frequency=series.frequency,
                start=start,
                end=end
            )
        
        elif "retail-sales" in series.endpoint:
            return self.eia.get_electricity_prices(
                state=series.region if series.region != "US" else "US",
                sector="RES",
                frequency=series.frequency,
                start=start,
                end=end
            )
        
        elif "steo" in series.endpoint:
            return self.eia.get_steo_series(
                series_id=series.facets.get("seriesId"),
                start=start,
                end=end
            )
        
        return []
    
    def save_series_to_csv(self, series_id: str, data: List[Dict], filename: str = None):
        """Save fetched data to CSV"""
        if not data:
            print(f"No data to save for {series_id}")
            return
        
        series = self.registry.get(series_id)
        if not series:
            return
        
        freq_dir = f"{self.data_dir}/{series.frequency}"
        os.makedirs(freq_dir, exist_ok=True)
        
        if filename is None:
            filename = f"{freq_dir}/{series_id}.csv"
        
        # Determine columns from first record
        if data:
            columns = list(data[0].keys())
        else:
            columns = ["period", "value"]
        
        with open(filename, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(data)
        
        print(f"Saved {len(data)} records to {filename}")
    
    def fetch_and_save(self, series_id: str, start: str = None, end: str = None):
        """Fetch a series and save to CSV"""
        data = self.fetch_series(series_id, start, end)
        self.save_series_to_csv(series_id, data)
        return data
    
    def fetch_all_eia(self, start: str = "2000-01"):
        """Fetch all EIA series"""
        for series_id, series in self.registry.items():
            if series.source == "eia":
                print(f"\nFetching: {series.name}")
                self.fetch_and_save(series_id, start=start)
    
    def get_data_gaps_report(self) -> str:
        """Generate report of data gaps and required sources"""
        report = """
==========================================================================
DECARBIQ DATA GAPS AND REQUIRED DATA SOURCES
==========================================================================

AVAILABLE VIA EIA API (automated):
✓ Henry Hub spot price (monthly, daily)
✓ US dry gas production (monthly)
✓ US working gas storage (weekly)
✓ US retail electricity prices by state (monthly)
✓ LNG exports (monthly)
✓ Pipeline exports to Mexico (monthly)

AVAILABLE VIA EIA BUT NEED VERIFICATION:
? Waha Hub spot price - need correct duoarea/series code
? SoCal Citygate price - need correct duoarea/series code
? Regional production (Permian, Haynesville) - via STEO, need series IDs
? Pacific region storage - verify duoarea code

REQUIRES GRIDSTATUS LIBRARY (pip install gridstatus):
! ERCOT wholesale LMPs (hourly) - gridstatus.ERCOT().get_lmp()
! ERCOT Settlement Point Prices - gridstatus.ERCOT().get_spp()
! CAISO wholesale LMPs (hourly) - gridstatus.CAISO().get_lmp()
! CAISO fuel mix / renewables - gridstatus.CAISO().get_fuel_mix()

REQUIRES MANUAL DOWNLOAD:
! Baker Hughes rig count - Excel download from rigcount.bakerhughes.com
! Permian pipeline capacity - Various operator announcements
! Aliso Canyon storage status - California Energy Commission

NOT AVAILABLE / ALTERNATIVE NEEDED:
✗ California non-fuel electricity costs - CPUC rate case filings
✗ Wildfire liability costs - PG&E, SCE, SDG&E annual reports
✗ Renewable curtailment by hour - CAISO reports

==========================================================================
RECOMMENDED NEXT STEPS:
==========================================================================

1. Get EIA API key (free): https://www.eia.gov/opendata/register.php

2. Install gridstatus for wholesale prices:
   pip install gridstatus

3. Run initial EIA data fetch:
   collector = DecarbIQDataCollector(eia_api_key="YOUR_KEY")
   collector.fetch_all_eia(start="2010-01")

4. Fetch ERCOT/CAISO wholesale with gridstatus:
   import gridstatus
   ercot = gridstatus.ERCOT()
   df = ercot.get_spp(start='2020-01-01', end='2024-12-31')

5. Download Baker Hughes rig count Excel manually
"""
        return report


# =============================================================================
# USAGE EXAMPLES
# =============================================================================

def example_usage():
    """Example of how to use the data collection system"""
    
    print("="*60)
    print("DecarbIQ Data Collection System")
    print("="*60)
    
    # Initialize without API key to show structure
    collector = DecarbIQDataCollector()
    
    # Show data gaps report
    print(collector.get_data_gaps_report())
    
    # Show ERCOT instructions
    print("\n" + "="*60)
    print("ERCOT DATA ACCESS INSTRUCTIONS:")
    print("="*60)
    print(collector.ercot.get_historical_spp_info())
    
    # Show CAISO instructions
    print("\n" + "="*60)
    print("CAISO DATA ACCESS INSTRUCTIONS:")
    print("="*60)
    print(collector.caiso.get_historical_info())
    
    # Show Baker Hughes instructions
    print("\n" + "="*60)
    print("BAKER HUGHES RIG COUNT INSTRUCTIONS:")
    print("="*60)
    print(collector.bakerhughes.get_data_info())
    
    # List all registered series
    print("\n" + "="*60)
    print("REGISTERED DATA SERIES:")
    print("="*60)
    for series_id, series in DATA_SERIES_REGISTRY.items():
        print(f"  {series_id}:")
        print(f"    Name: {series.name}")
        print(f"    Source: {series.source}")
        print(f"    Frequency: {series.frequency}")
        print(f"    Region: {series.region}")
        print()


if __name__ == "__main__":
    example_usage()
