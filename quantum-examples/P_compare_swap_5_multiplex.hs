import BellKAT.QuantumPrelude hiding (lookup)
import qualified Data.Map.Strict as Map
import GHC.Exts (fromList)
import Data.List (intercalate, stripPrefix)
import System.Environment (getArgs, withArgs)
import Text.Read (readMaybe)

data Scenario = Scenario
    { scProtocolName :: String
    , scEventName :: String
    , scPGen :: Double
    , scPSwap :: Double
    , scW0 :: Double
    , scTCoh :: Int
    , scMultiplexing :: Int
    , scEdgeSkew :: Double
    }

defaultScenario :: Scenario
defaultScenario = Scenario
    { scProtocolName = "swap-asap"
    , scEventName = "pure"
    , scPGen = 8.2e-1
    , scPSwap = 1 / 2
    , scW0 = 0.961
    , scTCoh = 7200
    , scMultiplexing = 1
    , scEdgeSkew = 1
    }

parallelCopies :: Int -> QBKATPolicy -> QBKATPolicy
parallelCopies n policy
    | n <= 0 = mempty
    | otherwise = foldr1 (<||>) (replicate n policy)

attemptsFor :: Int -> QBKATTest -> (Location, Location) -> QBKATPolicy
attemptsFor multiplexing guard edge =
    parallelCopies multiplexing (ite guard (ucreate edge) mempty)

-- | Try elementary-link generation in parallel. Multiplexing is represented by
-- repeated parallel create attempts for each elementary edge; NetworkCapacity
-- then keeps at most one pair per Bell-pair identity.
pGen :: Int -> QBKATPolicy
pGen multiplexing =
            attemptsFor
                multiplexing
                ("A" /~? "X" &&* "A" /~? "Y" &&* "A" /~? "Z")
                ("A", "X")
        <||>
            attemptsFor
                multiplexing
                ("X" /~? "Y" &&* "A" /~? "Y" &&* "X" /~? "Z" &&* "A" /~? "Z" &&* "X" /~? "E")
                ("X", "Y")
        <||>
            attemptsFor
                multiplexing
                ("Y" /~? "Z" &&* "X" /~? "Z" &&* "Y" /~? "E" &&* "A" /~? "Z" &&* "X" /~? "E")
                ("Y", "Z")
        <||>
            attemptsFor
                multiplexing
                ("Z" /~? "E" &&* "Y" /~? "E" &&* "X" /~? "E")
                ("Z", "E")

pSwapASAP :: QBKATPolicy
pSwapASAP =
        sswap ["X", "Y", "Z"] ("A", "E")
        <.>
        (
                sswap ["X", "Z"] ("A", "E")
            <||>
                sswap ["X", "Y"] ("A", "E")
            <||>
                sswap ["Y", "Z"] ("A", "E")
            <||>
                sswap ["X", "Y"] ("A", "Z")
            <||>
                sswap ["Y", "Z"] ("X", "E")
        )
        <.>
        (
                swap "X" ("A", "E")
            <||>
                swap "Y" ("A", "E")
            <||>
                swap "Z" ("A", "E")
            <||>
                swap "X" ("A", "Z")
            <||>
                swap "Y" ("A", "Z")
            <||>
                swap "Z" ("X", "E")
            <||>
                swap "Y" ("X", "E")
            <||>
                swap "X" ("A", "Y")
            <||>
                swap "Y" ("X", "Z")
            <||>
                swap "Z" ("Y", "E")
        )

pASAP :: Int -> QBKATPolicy
pASAP multiplexing = while ("A" /~? "E")
    (
        pGen multiplexing
        <>
        pSwapASAP
        <>
        pSwapASAP
        <>
        pSwapASAP
    )

pSeq1 :: Int -> QBKATPolicy
pSeq1 multiplexing = while ("A" /~? "E")
    (
        pGen multiplexing
        <>
        swap "X" ("A", "Y")
        <>
        swap "Y" ("A", "Z")
        <>
        swap "Z" ("A", "E")
    )

pSeq2 :: Int -> QBKATPolicy
pSeq2 multiplexing = while ("A" /~? "E")
    (
        pGen multiplexing
        <>
        swap "Z" ("Y", "E")
        <>
        swap "Y" ("X", "E")
        <>
        swap "X" ("A", "E")
    )

pSim :: Int -> QBKATPolicy
pSim multiplexing = while ("A" /~? "E")
    (
        pGen multiplexing
        <>
        sswap ["X", "Y", "Z"] ("A", "E")
    )

pDoubling :: Int -> QBKATPolicy
pDoubling multiplexing = while ("A" /~? "E")
    (
        pGen multiplexing
        <>
        swap "X" ("A", "Y")
        <>
        swap "Z" ("Y", "E")
        <>
        swap "Y" ("A", "E")
    )

protocols :: Int -> [(String, QBKATPolicy)]
protocols multiplexing =
    [ ("swap-asap", pASAP multiplexing)
    , ("asap", pASAP multiplexing)
    , ("left-to-right", pSeq1 multiplexing)
    , ("right-to-left", pSeq2 multiplexing)
    , ("at-last", pSim multiplexing)
    , ("doubling", pDoubling multiplexing)
    ]

events :: [(String, QBKATTest)]
events =
    [ ("static", "A" ~~? "E")
    , ("pure", "A" -~? "E")
    , ("mixed", "A" =~? "E")
    ]

selectProtocol :: Int -> String -> Either String QBKATPolicy
selectProtocol multiplexing name =
    maybe
        (Left $ "Unknown protocol '" <> name <> "'. Available protocols: " <> availableProtocols)
        Right
        (lookup name (protocols multiplexing))

selectEvent :: String -> Either String QBKATTest
selectEvent name =
    maybe
        (Left $ "Unknown event '" <> name <> "'. Available events: " <> availableEvents)
        Right
        (lookup name events)

availableProtocols :: String
availableProtocols = intercalate ", " ["swap-asap", "asap", "left-to-right", "right-to-left", "at-last", "doubling"]

availableEvents :: String
availableEvents = intercalate ", " (fmap fst events)

readFlag :: Read a => String -> String -> Either String a
readFlag flag raw =
    case readMaybe raw of
        Nothing -> Left $ "Could not parse " <> flag <> " value '" <> raw <> "'."
        Just value -> Right value

setDouble :: String -> (Double -> Scenario -> Scenario) -> String -> Scenario -> Either String Scenario
setDouble flag setter raw scenario =
    fmap (`setter` scenario) (readFlag flag raw)

setInt :: String -> (Int -> Scenario -> Scenario) -> String -> Scenario -> Either String Scenario
setInt flag setter raw scenario =
    fmap (`setter` scenario) (readFlag flag raw)

stripExampleArgs :: [String] -> Either String (Scenario, [String])
stripExampleArgs = go defaultScenario []
  where
    go scenario kept [] =
        validateScenario scenario *> Right (scenario, reverse kept)
    go _ _ ["--protocol"] = Left "Missing value for --protocol."
    go _ _ ["--event"] = Left "Missing value for --event."
    go _ _ ["--p-gen"] = Left "Missing value for --p-gen."
    go _ _ ["--p-swap"] = Left "Missing value for --p-swap."
    go _ _ ["--w0"] = Left "Missing value for --w0."
    go _ _ ["--t-coh"] = Left "Missing value for --t-coh."
    go _ _ ["--multiplexing"] = Left "Missing value for --multiplexing."
    go _ _ ["--edge-skew"] = Left "Missing value for --edge-skew."
    go scenario kept ("--protocol" : name : rest) =
        go scenario{scProtocolName = name} kept rest
    go scenario kept ("--event" : name : rest) =
        go scenario{scEventName = name} kept rest
    go scenario kept ("--p-gen" : raw : rest) =
        setDouble "--p-gen" (\value sc -> sc{scPGen = value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--p-swap" : raw : rest) =
        setDouble "--p-swap" (\value sc -> sc{scPSwap = value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--w0" : raw : rest) =
        setDouble "--w0" (\value sc -> sc{scW0 = value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--t-coh" : raw : rest) =
        setInt "--t-coh" (\value sc -> sc{scTCoh = value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--multiplexing" : raw : rest) =
        setInt "--multiplexing" (\value sc -> sc{scMultiplexing = value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--edge-skew" : raw : rest) =
        setDouble "--edge-skew" (\value sc -> sc{scEdgeSkew = value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept (arg : rest)
        | Just name <- stripPrefix "--protocol=" arg =
            go scenario{scProtocolName = name} kept rest
        | Just name <- stripPrefix "--event=" arg =
            go scenario{scEventName = name} kept rest
        | Just raw <- stripPrefix "--p-gen=" arg =
            setDouble "--p-gen" (\value sc -> sc{scPGen = value}) raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--p-swap=" arg =
            setDouble "--p-swap" (\value sc -> sc{scPSwap = value}) raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--w0=" arg =
            setDouble "--w0" (\value sc -> sc{scW0 = value}) raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--t-coh=" arg =
            setInt "--t-coh" (\value sc -> sc{scTCoh = value}) raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--multiplexing=" arg =
            setInt "--multiplexing" (\value sc -> sc{scMultiplexing = value}) raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--edge-skew=" arg =
            setDouble "--edge-skew" (\value sc -> sc{scEdgeSkew = value}) raw scenario >>= \updated ->
                go updated kept rest
        | otherwise =
            go scenario (arg : kept) rest

validateScenario :: Scenario -> Either String ()
validateScenario scenario
    | scPGen scenario <= 0 || scPGen scenario > 1 =
        Left "--p-gen must be in the interval (0, 1]."
    | scPSwap scenario < 0 || scPSwap scenario > 1 =
        Left "--p-swap must be in the interval [0, 1]."
    | scW0 scenario < 0 || scW0 scenario > 1 =
        Left "--w0 must be in the interval [0, 1]."
    | scTCoh scenario <= 0 =
        Left "--t-coh must be positive."
    | scMultiplexing scenario <= 0 =
        Left "--multiplexing must be positive."
    | scEdgeSkew scenario < 1 =
        Left "--edge-skew must be at least 1; 1 is homogeneous."
    | otherwise =
        Right ()

doublingPathNodes :: [Location]
doublingPathNodes = ["A", "X", "Y", "Z", "E"]

pathPairs :: [(Location, Location)]
pathPairs =
    [ (left, right)
    | (index, left) <- zip [(0 :: Int)..] doublingPathNodes
    , right <- drop (index + 1) doublingPathNodes
    ]

capacityPairs :: [(Location, Location)]
capacityPairs = physicalTopologyLinks <> pathPairs

networkCapacity :: NetworkCapacity QBKATTag
networkCapacity = fromList [left ~ right | (left, right) <- capacityPairs]

nb :: NetworkBounds QBKATTag
nb = def { nbCapacity = Just networkCapacity }

physicalTopologyLinks :: [(Location, Location)]
physicalTopologyLinks =
    [ ("A", "X")
    , ("B", "X")
    , ("X", "Y")
    , ("X", "C")
    , ("A", "C")
    , ("C", "Y")
    , ("Y", "Z")
    , ("Y", "D")
    , ("Z", "E")
    ]

topologyDistances :: [((Location, Location), Int)]
topologyDistances =
    [ (("A", "B"), 4)
    , (("A", "C"), 6)
    , (("A", "D"), 10)
    , (("A", "E"), 14)
    , (("A", "X"), 2)
    , (("A", "Y"), 7)
    , (("A", "Z"), 12)
    , (("B", "C"), 8)
    , (("B", "D"), 10)
    , (("B", "E"), 14)
    , (("B", "X"), 2)
    , (("B", "Y"), 7)
    , (("B", "Z"), 12)
    , (("C", "D"), 7)
    , (("C", "E"), 11)
    , (("C", "X"), 6)
    , (("C", "Y"), 4)
    , (("C", "Z"), 9)
    , (("D", "E"), 10)
    , (("D", "X"), 8)
    , (("D", "Y"), 3)
    , (("D", "Z"), 8)
    , (("E", "X"), 12)
    , (("E", "Y"), 7)
    , (("E", "Z"), 2)
    , (("X", "Y"), 5)
    , (("X", "Z"), 10)
    , (("Y", "Z"), 5)
    ]

lookupDistanceUnits :: (Location, Location) -> Int
lookupDistanceUnits edge@(left, right) =
    case lookup edge topologyDistances of
        Just distance -> distance
        Nothing ->
            case lookup (right, left) topologyDistances of
                Just distance -> distance
                Nothing -> error $ "missing topology distance for " <> show edge

hardwarePGen :: Int -> Double
hardwarePGen units =
    case units of
        1 -> 8.2e-1
        2 -> 2.6e-2
        3 -> 1.83e-2
        4 -> 1.30e-2
        5 -> 9.2e-3
        6 -> 6.52e-3
        -- 1 -> 6.06e-1
        -- 2 -> 0.36e-1
        -- 3 -> 0.22e-1
        -- 4 -> 1.35e-1
        -- 5 -> 8.20e-2
        -- 6 -> 4.97e-2
        _ -> error $ "missing p_gen hardware point for length " <> show (10 * units) <> " km"

hardwareW0 :: Int -> Double
hardwareW0 units =
    case units of
        1 -> 0.961
        2 -> 0.958
        3 -> 0.956
        4 -> 0.954
        5 -> 0.952
        6 -> 0.950
        _ -> error $ "missing w0 hardware point for length " <> show (10 * units) <> " km"

clamp :: Double -> Double -> Double -> Double
clamp lower upper = min upper . max lower

edgePGen :: Scenario -> (Location, Location) -> Double
edgePGen scenario edge =
    clamp 0 1 $
        scPGen scenario
        * hardwarePGen distanceUnits
        / hardwarePGen 1
        / skewPenalty
  where
    distanceUnits = lookupDistanceUnits edge
    skewPenalty =
        if edge == ("Z", "E") || edge == ("E", "Z")
        then scEdgeSkew scenario
        else 1

edgeW0 :: Scenario -> (Location, Location) -> Double
edgeW0 scenario edge =
    clamp 0 1 $
        scW0 scenario
        + hardwareW0 distanceUnits
        - hardwareW0 1
  where
    distanceUnits = lookupDistanceUnits edge

actionConfig :: Scenario -> ProbabilisticActionConfiguration
actionConfig scenario = PAC
    { pacTransmitProbability = []
    , pacCreateProbability = []
    , pacCreateWerner = []
    , pacUCreateProbability = Map.fromList
        [ (edge, toRational (edgePGen scenario edge))
        | edge <- physicalTopologyLinks
        ]
    , pacUCreateWerner = Map.fromList
        [ (edge, edgeW0 scenario edge)
        | edge <- physicalTopologyLinks
        ]
    , pacSwapProbability =
        [ ("X", pSwap)
        , ("Y", pSwap)
        , ("Z", pSwap)
        ]
    , pacCoherenceTime =
        [ ("A", scTCoh scenario)
        , ("B", scTCoh scenario)
        , ("C", scTCoh scenario)
        , ("D", scTCoh scenario)
        , ("E", scTCoh scenario)
        , ("X", scTCoh scenario)
        , ("Y", scTCoh scenario)
        , ("Z", scTCoh scenario)
        ]
    , pacDistances = Map.fromList topologyDistances
    }
  where
    pSwap = toRational (scPSwap scenario)

main :: IO ()
main = do
    args <- getArgs
    (scenario, qbkatArgs) <-
        either fail pure (stripExampleArgs args)
    protocol <- either fail pure (selectProtocol (scMultiplexing scenario) (scProtocolName scenario))
    ev <- either fail pure (selectEvent (scEventName scenario))
    withArgs qbkatArgs $
        qbkatMainD (actionConfig scenario) nb ev protocol mempty
