"""Standard SNMP OID constants (RFC 1213, Q-BRIDGE-MIB)."""

# MIB-II system group
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"

# MIB-II interfaces group
IF_NUMBER = "1.3.6.1.2.1.2.1.0"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
IF_IN_UCAST_PKTS = "1.3.6.1.2.1.2.2.1.11"
IF_OUT_UCAST_PKTS = "1.3.6.1.2.1.2.2.1.17"

# IF-MIB 64-bit counters
IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"

# Q-BRIDGE-MIB VLAN tables
DOT1Q_VLAN_NUM = "1.3.6.1.2.1.17.7.1.4.1.0"
# dot1qVlanCurrentTable (indexed by dot1qVlanTimeMark, dot1qVlanIndex)
DOT1Q_VLAN_CURRENT_EGRESS = "1.3.6.1.2.1.17.7.1.4.2.1.4"
DOT1Q_VLAN_CURRENT_UNTAGGED = "1.3.6.1.2.1.17.7.1.4.2.1.5"
# dot1qVlanStaticTable (indexed by dot1qVlanIndex)
DOT1Q_VLAN_STATIC_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"
DOT1Q_VLAN_STATIC_EGRESS = "1.3.6.1.2.1.17.7.1.4.3.1.2"
DOT1Q_VLAN_STATIC_UNTAGGED = "1.3.6.1.2.1.17.7.1.4.3.1.4"
DOT1Q_PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"

# Bridge port mapping (maps bridge port index to ifIndex)
DOT1D_BASE_PORT_IF_INDEX = "1.3.6.1.2.1.17.1.4.1.2"

# IF-MAU-MIB — ifMauType (copper vs fiber / SFP for combo ports)
IF_MAU_TYPE = "1.3.6.1.2.1.26.2.1.1.3"

# POWER-ETHERNET-MIB (RFC 3621) — pethPsePortTable
# Indexed by pethPsePortGroupIndex.pethPsePortIndex (port index often == ifIndex)
PETH_PSE_PORT_ADMIN_ENABLE = "1.3.6.1.2.1.105.1.1.1.3"
PETH_PSE_PORT_DETECTION_STATUS = "1.3.6.1.2.1.105.1.1.1.6"
PETH_PSE_PORT_POWER_PRIORITY = "1.3.6.1.2.1.105.1.1.1.7"
PETH_PSE_PORT_POWER_CLASSIFICATIONS = "1.3.6.1.2.1.105.1.1.1.10"

# HP-ICF-POE-MIB — per-port power in milliwatts (HPE / ProCurve / Instant On)
HPICF_POE_PORT_POWER = "1.3.6.1.4.1.11.2.14.11.1.9.1.1.1.3"
HPICF_POE_PORT_ACTUAL_POWER = "1.3.6.1.4.1.11.2.14.11.1.9.1.1.1.8"
