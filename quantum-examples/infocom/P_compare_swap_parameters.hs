import Data.List (stripPrefix)
import qualified Data.Map.Strict as Map
import Common.NetworkConfig
    ( NetworkParameters(..)
    , defaultNetworkParameters
    , withUniformCoherenceTime
    , withUniformSwapProbability
    )
import Common.SwapSchemes
    ( defaultEventName
    , defaultProtocolName
    , runSwapScheme
    )
import System.Environment (getArgs)
import Text.Read (readMaybe)

data Scenario = Scenario
    { scProtocolName :: String
    , scEventName :: String
    , scNetworkParameters :: NetworkParameters
    }

defaultScenario :: Scenario
defaultScenario = Scenario
    { scProtocolName = defaultProtocolName
    , scEventName = defaultEventName
    , scNetworkParameters = defaultNetworkParameters
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
setPGen value = withParameters (\parameters -> parameters{npReferencePGen = value})

setPSwap :: Double -> Scenario -> Scenario
setPSwap value = withParameters (withUniformSwapProbability value)

setW0 :: Double -> Scenario -> Scenario
setW0 value = withParameters (\parameters -> parameters{npReferenceW0 = value})

setTCoh :: Int -> Scenario -> Scenario
setTCoh value = withParameters (withUniformCoherenceTime value)

setEdgeSkew :: Double -> Scenario -> Scenario
setEdgeSkew value = withParameters (\parameters -> parameters{npEdgeSkew = value})

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
    | npReferencePGen parameters <= 0 || npReferencePGen parameters > 1 =
        Left "--p-ge/--p-gen must be a 50 km reference probability in the interval (0, 1]."
    | any invalidProbability (Map.elems (npSwapProbabilities parameters)) =
        Left "--p-swap must be in the interval [0, 1]."
    | npReferenceW0 parameters < 0 || npReferenceW0 parameters > 1 =
        Left "--w0 must be a 50 km reference Werner parameter in the interval [0, 1]."
    | any (<= 0) (Map.elems (npCoherenceTimes parameters)) =
        Left "--t-coh must be positive."
    | npEdgeSkew parameters < 1 =
        Left "--edge-skew must be at least 1; 1 is homogeneous."
    | otherwise =
        Right ()
  where
    parameters = scNetworkParameters scenario
    invalidProbability value = value < 0 || value > 1

main :: IO ()
main = do
    args <- getArgs
    (scenario, qbkatArgs) <- either fail pure (stripExampleArgs args)
    runSwapScheme
        (scNetworkParameters scenario)
        (scProtocolName scenario)
        (scEventName scenario)
        qbkatArgs
