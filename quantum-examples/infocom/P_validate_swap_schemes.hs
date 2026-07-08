import BellKAT.QuantumPrelude
import Data.List (stripPrefix)
import qualified Data.Map.Strict as Map
import Common.NetworkConfig
    ( NetworkParameters(..)
    , allTopologyNodes
    , defaultNetworkParameters
    , doublingPathNodes
    , pathElementaryLinks
    , repeaterNodes
    , withUniformCoherenceTime
    , withUniformSwapProbability
    )
import Common.SwapSchemes
    ( defaultEventName
    , defaultProtocolName
    , networkBounds
    , runSwapSchemeWithActionConfigAndBounds
    , runSwapSchemeWithBounds
    , swapActionConfig
    )
import System.Environment (getArgs)
import Text.Read (readMaybe)

data Scenario = Scenario
    { scProtocolName :: String
    , scEventName :: String
    , scNetworkParameters :: NetworkParameters
    , scHomogeneousLinks :: Bool
    , scIgnoreEndpointDecoherence :: Bool
    , scPGen :: Maybe Double
    , scPSwap :: Maybe Double
    , scW0 :: Maybe Double
    }

defaultScenario :: Scenario
defaultScenario = Scenario
    { scProtocolName = defaultProtocolName
    , scEventName = defaultEventName
    , scNetworkParameters = defaultNetworkParameters
    , scHomogeneousLinks = False
    , scIgnoreEndpointDecoherence = False
    , scPGen = Nothing
    , scPSwap = Nothing
    , scW0 = Nothing
    }

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

withParameters :: (NetworkParameters -> NetworkParameters) -> Scenario -> Scenario
withParameters setter scenario =
    scenario{scNetworkParameters = setter (scNetworkParameters scenario)}

setPGen :: Double -> Scenario -> Scenario
setPGen value scenario =
    (withParameters (\parameters -> parameters{npReferencePGen = value}) scenario)
        { scPGen = Just value
        }

setPSwap :: Double -> Scenario -> Scenario
setPSwap value scenario =
    (withParameters (withUniformSwapProbability value) scenario)
        { scPSwap = Just value
        }

setW0 :: Double -> Scenario -> Scenario
setW0 value scenario =
    (withParameters (\parameters -> parameters{npReferenceW0 = value}) scenario)
        { scW0 = Just value
        }

setTCoh :: Int -> Scenario -> Scenario
setTCoh value =
    withParameters (withUniformCoherenceTime value)

setEdgeSkew :: Double -> Scenario -> Scenario
setEdgeSkew value =
    withParameters (\parameters -> parameters{npEdgeSkew = value})

setPaperAssumptions :: Scenario -> Scenario
setPaperAssumptions scenario =
    setPSwap 1 $
    setW0 1 $
    scenario
        { scHomogeneousLinks = True
        , scIgnoreEndpointDecoherence = True
        }

stripExampleArgs :: [String] -> Either String (Scenario, [String])
stripExampleArgs = go defaultScenario []
  where
    go scenario kept [] =
        validateScenario scenario *> Right (scenario, reverse kept)
    go _ _ ["--protocol"] = Left "Missing value for --protocol."
    go _ _ ["--event"] = Left "Missing value for --event."
    go _ _ ["--p-ge"] = Left "Missing value for --p-ge."
    go _ _ ["--p-gen"] = Left "Missing value for --p-gen."
    go _ _ ["--p-swap"] = Left "Missing value for --p-swap."
    go _ _ ["--w0"] = Left "Missing value for --w0."
    go _ _ ["--t-coh"] = Left "Missing value for --t-coh."
    go _ _ ["--edge-skew"] = Left "Missing value for --edge-skew."
    go scenario kept ("--protocol" : name : rest) =
        go scenario{scProtocolName = name} kept rest
    go scenario kept ("--event" : name : rest) =
        go scenario{scEventName = name} kept rest
    go scenario kept ("--paper-assumptions" : rest) =
        go (setPaperAssumptions scenario) kept rest
    go scenario kept ("--homogeneous-links" : rest) =
        go scenario{scHomogeneousLinks = True} kept rest
    go scenario kept ("--ignore-endpoint-decoherence" : rest) =
        go scenario{scIgnoreEndpointDecoherence = True} kept rest
    go scenario kept ("--p-ge" : raw : rest) =
        setDouble "--p-ge" setPGen raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--p-gen" : raw : rest) =
        setDouble "--p-gen" setPGen raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--p-swap" : raw : rest) =
        setDouble "--p-swap" setPSwap raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--w0" : raw : rest) =
        setDouble "--w0" setW0 raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--t-coh" : raw : rest) =
        setInt "--t-coh" setTCoh raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--edge-skew" : raw : rest) =
        setDouble "--edge-skew" setEdgeSkew raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept (arg : rest)
        | Just name <- stripPrefix "--protocol=" arg =
            go scenario{scProtocolName = name} kept rest
        | Just name <- stripPrefix "--event=" arg =
            go scenario{scEventName = name} kept rest
        | Just raw <- stripPrefix "--p-ge=" arg =
            setDouble "--p-ge" setPGen raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--p-gen=" arg =
            setDouble "--p-gen" setPGen raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--p-swap=" arg =
            setDouble "--p-swap" setPSwap raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--w0=" arg =
            setDouble "--w0" setW0 raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--t-coh=" arg =
            setInt "--t-coh" setTCoh raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--edge-skew=" arg =
            setDouble "--edge-skew" setEdgeSkew raw scenario >>= \updated ->
                go updated kept rest
        | otherwise =
            go scenario (arg : kept) rest

validateScenario :: Scenario -> Either String ()
validateScenario scenario
    | maybe False invalidOpenProbability (scPGen scenario) =
        Left "--p-ge/--p-gen must be in the interval (0, 1)."
    | any invalidProbability (Map.elems (npSwapProbabilities parameters)) =
        Left "--p-swap must be in the interval [0, 1]."
    | npReferenceW0 parameters < 0 || npReferenceW0 parameters > 1 =
        Left "--w0 must be in the interval [0, 1]."
    | any (<= 0) (Map.elems (npCoherenceTimes parameters)) =
        Left "--t-coh must be positive."
    | npEdgeSkew parameters < 1 =
        Left "--edge-skew must be at least 1; 1 is homogeneous."
    | otherwise =
        Right ()
  where
    parameters = scNetworkParameters scenario
    invalidOpenProbability value = value <= 0 || value >= 1
    invalidProbability value = value < 0 || value > 1

validationNetworkBounds :: NetworkBounds QBKATTag
validationNetworkBounds = networkBounds { nbOperationTiming = InstantaneousOps }

noDecoherenceTime :: Int
noDecoherenceTime = 1000000000000000

scenarioActionConfig :: Scenario -> ProbabilisticActionConfiguration
scenarioActionConfig scenario
    | scHomogeneousLinks scenario =
        homogeneousActionConfig scenario
    | scIgnoreEndpointDecoherence scenario =
        (swapActionConfig parameters)
            { pacCoherenceTime = validationCoherenceTimes scenario
            }
    | otherwise =
        swapActionConfig parameters
  where
    parameters = scNetworkParameters scenario

homogeneousActionConfig :: Scenario -> ProbabilisticActionConfiguration
homogeneousActionConfig scenario = PAC
    { pacTransmitProbability = Map.empty
    , pacCreateProbability = Map.empty
    , pacCreateWerner = Map.empty
    , pacUCreateProbability = Map.fromList
        [ (edge, toRational pGen)
        | edge <- pathElementaryLinks
        ]
    , pacUCreateWerner = Map.fromList
        [ (edge, w0)
        | edge <- pathElementaryLinks
        ]
    , pacSwapProbability = Map.fromList
        [ (location, toRational pSwap)
        | location <- repeaterNodes
        ]
    , pacCoherenceTime = validationCoherenceTimes scenario
    , pacDistances = Map.fromList paperPathDistances
    }
  where
    parameters = scNetworkParameters scenario
    pGen = maybe (npReferencePGen parameters) id (scPGen scenario)
    w0 = maybe (npReferenceW0 parameters) id (scW0 scenario)
    pSwap =
        maybe
            (Map.findWithDefault 1 "X" (npSwapProbabilities parameters))
            id
            (scPSwap scenario)

validationCoherenceTimes :: Scenario -> Map.Map Location Int
validationCoherenceTimes scenario
    | scIgnoreEndpointDecoherence scenario =
        Map.insert "A" noDecoherenceTime $
        Map.insert "E" noDecoherenceTime base
    | otherwise =
        base
  where
    parameters = scNetworkParameters scenario
    base =
        Map.union
            (npCoherenceTimes parameters)
            (Map.fromList [(location, 1440000) | location <- allTopologyNodes])

paperPathDistances :: [((Location, Location), Int)]
paperPathDistances =
    [ ((left, right), rightIndex - leftIndex)
    | (leftIndex, left) <- zip [(0 :: Int)..] doublingPathNodes
    , (rightIndex, right) <- zip [(0 :: Int)..] doublingPathNodes
    , leftIndex < rightIndex
    ]

main :: IO ()
main = do
    args <- getArgs
    (scenario, qbkatArgs) <- either fail pure (stripExampleArgs args)
    if scHomogeneousLinks scenario || scIgnoreEndpointDecoherence scenario
       then
            runSwapSchemeWithActionConfigAndBounds
                (scenarioActionConfig scenario)
                validationNetworkBounds
                (scProtocolName scenario)
                (scEventName scenario)
                qbkatArgs
       else
            runSwapSchemeWithBounds
                (scNetworkParameters scenario)
                validationNetworkBounds
                (scProtocolName scenario)
                (scEventName scenario)
                qbkatArgs
